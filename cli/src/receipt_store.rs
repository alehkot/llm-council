//! Experimental offline storage primitives for future idempotent submission.
//!
//! No command, transport or API version is connected. The caller must validate
//! backend acknowledgment and persist a pending request before any first send.
//! Not safe for live use: macOS durability, ACL and stable root/reopen semantics
//! are unresolved, among the limitations documented in README.md.

use crate::config::BackendOrigin;
use crate::wire::{CreateRunRequest, RunReceipt};
use serde::{Deserialize, Serialize};
use std::ffi::CString;
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use uuid::Uuid;

const MAX_RECORD_BYTES: u64 = 16 * 1024 * 1024;
static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);
#[cfg(test)]
static FAIL_SYNC: std::sync::atomic::AtomicU8 = std::sync::atomic::AtomicU8::new(0);

fn sync_file(file: &File) -> io::Result<()> {
    #[cfg(test)]
    if FAIL_SYNC
        .compare_exchange(1, 0, Ordering::SeqCst, Ordering::SeqCst)
        .is_ok()
    {
        return Err(io::Error::other("injected file sync failure"));
    }
    file.sync_all()
}
fn sync_directory(file: &File) -> io::Result<()> {
    #[cfg(test)]
    if FAIL_SYNC
        .compare_exchange(2, 0, Ordering::SeqCst, Ordering::SeqCst)
        .is_ok()
    {
        return Err(io::Error::other("injected directory sync failure"));
    }
    file.sync_all()
}

/// Sanitized errors contain neither request contents nor filesystem paths.
#[derive(Debug, PartialEq, Eq)]
pub enum StoreError {
    UnsafeDirectory,
    UnsafeFile,
    Busy,
    Missing,
    Conflict,
    InvalidRecord,
    TooLarge,
    Io,
}
impl From<io::Error> for StoreError {
    fn from(_: io::Error) -> Self {
        Self::Io
    }
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case", deny_unknown_fields)]
pub enum SubmissionState {
    Pending { payload: String },
    Acknowledged { receipt: RunReceipt },
}
// User-supplied questions and negative prompts must not appear in debug logs.
impl std::fmt::Debug for SubmissionState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Pending { .. } => f.write_str("Pending { payload: [redacted] }"),
            Self::Acknowledged { .. } => f.write_str("Acknowledged { receipt: [redacted] }"),
        }
    }
}
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    version: u8,
    run_id: Uuid,
    origin: String,
    submission: SubmissionState,
}

/// An open descriptor pins the validated directory even if an ancestor is renamed.
/// All member accesses are relative to the descriptor and do not follow symlinks.
pub struct ReceiptStore {
    dir: File,
}

fn name(value: &str) -> Result<CString, StoreError> {
    CString::new(value).map_err(|_| StoreError::InvalidRecord)
}
fn open_relative(dir: &File, component: &str, flags: libc::c_int) -> io::Result<File> {
    let name = CString::new(component).map_err(|_| io::ErrorKind::InvalidInput)?;
    // SAFETY: name is NUL-terminated and the descriptor remains live. openat
    // returns a newly owned descriptor or -1 without transferring ownership.
    let fd = unsafe {
        libc::openat(
            dir.as_raw_fd(),
            name.as_ptr(),
            flags | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            0o600,
        )
    };
    if fd < 0 {
        Err(io::Error::last_os_error())
    } else {
        // SAFETY: successful openat returned a new descriptor owned here.
        Ok(unsafe { File::from_raw_fd(fd) })
    }
}
fn unlink_relative(dir: &File, component: &str) -> io::Result<()> {
    let name = CString::new(component).map_err(|_| io::ErrorKind::InvalidInput)?;
    // SAFETY: descriptor and C string remain live for the call.
    if unsafe { libc::unlinkat(dir.as_raw_fd(), name.as_ptr(), 0) } == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}
fn rename_relative(dir: &File, from: &str, to: &str) -> Result<(), StoreError> {
    let from = name(from)?;
    let to = name(to)?;
    // SAFETY: both strings and directory descriptor remain live during call.
    if unsafe { libc::renameat(dir.as_raw_fd(), from.as_ptr(), dir.as_raw_fd(), to.as_ptr()) } == 0
    {
        Ok(())
    } else {
        Err(StoreError::Io)
    }
}
fn safe_file(file: &File) -> Result<(), StoreError> {
    let meta = file.metadata()?;
    // SAFETY: geteuid has no arguments and does not access Rust memory.
    let uid = unsafe { libc::geteuid() };
    if !meta.is_file() || meta.uid() != uid || meta.mode() & 0o777 != 0o600 || meta.nlink() != 1 {
        return Err(StoreError::UnsafeFile);
    }
    Ok(())
}
impl ReceiptStore {
    /// Open an existing directory with owner-only POSIX mode bits. ACLs are not
    /// checked; do not use for live requests. The directory itself must
    /// not be a symlink. Future operations remain anchored to the opened inode.
    pub fn open(root: &Path) -> Result<Self, StoreError> {
        if !root.is_absolute() {
            return Err(StoreError::UnsafeDirectory);
        }
        let dir = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_DIRECTORY | libc::O_CLOEXEC)
            .open(root)
            .map_err(|_| StoreError::UnsafeDirectory)?;
        let meta = dir.metadata().map_err(|_| StoreError::UnsafeDirectory)?;
        // SAFETY: geteuid has no arguments and does not access Rust memory.
        let uid = unsafe { libc::geteuid() };
        if !meta.is_dir() || meta.uid() != uid || meta.mode() & 0o777 != 0o700 {
            return Err(StoreError::UnsafeDirectory);
        }
        Ok(Self { dir })
    }
    fn with_lock<T>(&self, f: impl FnOnce() -> Result<T, StoreError>) -> Result<T, StoreError> {
        // The lock is permanent; removing it would permit split lock domains.
        let lock = open_relative(&self.dir, ".lock", libc::O_RDWR | libc::O_CREAT)?;
        safe_file(&lock)?;
        lock.try_lock().map_err(|_| StoreError::Busy)?;
        f()
    }
    fn record_name(id: Uuid) -> String {
        format!("{id}.json")
    }
    fn read(&self, id: Uuid) -> Result<Option<Record>, StoreError> {
        let file = match open_relative(&self.dir, &Self::record_name(id), libc::O_RDWR) {
            Ok(file) => file,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(StoreError::Io),
        };
        safe_file(&file)?;
        // A prior sync may have failed, so repeat before a retry is permitted.
        sync_file(&file)?;
        let mut bytes = Vec::new();
        file.take(MAX_RECORD_BYTES + 1).read_to_end(&mut bytes)?;
        if bytes.len() as u64 > MAX_RECORD_BYTES {
            return Err(StoreError::TooLarge);
        }
        let record: Record =
            serde_json::from_slice(&bytes).map_err(|_| StoreError::InvalidRecord)?;
        let parsed = BackendOrigin::parse(&record.origin).map_err(|_| StoreError::InvalidRecord)?;
        if record.version != 1 || record.run_id != id || parsed.as_url().as_str() != record.origin {
            return Err(StoreError::InvalidRecord);
        }
        if let SubmissionState::Pending { payload } = &record.submission {
            let _: CreateRunRequest =
                serde_json::from_str(payload).map_err(|_| StoreError::InvalidRecord)?;
        }
        if let SubmissionState::Acknowledged { receipt } = &record.submission {
            if receipt.run_id != id {
                return Err(StoreError::InvalidRecord);
            }
        }
        Ok(Some(record))
    }
    fn encode(record: &Record) -> Result<Vec<u8>, StoreError> {
        let bytes = serde_json::to_vec(record).map_err(|_| StoreError::InvalidRecord)?;
        if bytes.len() as u64 > MAX_RECORD_BYTES {
            return Err(StoreError::TooLarge);
        }
        Ok(bytes)
    }
    fn initial(&self, record: &Record) -> Result<(), StoreError> {
        let bytes = Self::encode(record)?;
        // No request may be sent unless this completes. A crash while writing
        // may leave a partial file: that file is retained and fails closed.
        let mut file = open_relative(
            &self.dir,
            &Self::record_name(record.run_id),
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL,
        )?;
        safe_file(&file)?;
        file.write_all(&bytes)?;
        sync_file(&file)?;
        sync_directory(&self.dir)?;
        Ok(())
    }
    fn replace(&self, record: &Record) -> Result<(), StoreError> {
        let bytes = Self::encode(record)?;
        // These temporary records contain an acknowledged receipt, not payload.
        let temp = format!(
            ".ack-{}-{}",
            std::process::id(),
            TEMP_COUNTER.fetch_add(1, Ordering::Relaxed)
        );
        let mut file = open_relative(
            &self.dir,
            &temp,
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL,
        )?;
        let result = (|| {
            safe_file(&file)?;
            file.write_all(&bytes)?;
            sync_file(&file)?;
            rename_relative(&self.dir, &temp, &Self::record_name(record.run_id))?;
            sync_directory(&self.dir)?;
            Ok(())
        })();
        // Only remove a temporary file that this call actually created.
        let _ = unlink_relative(&self.dir, &temp);
        result
    }
    /// Persist the exact typed request before a future first submission.
    pub fn prepare(
        &self,
        id: Uuid,
        origin: &BackendOrigin,
        request: &CreateRunRequest,
    ) -> Result<SubmissionState, StoreError> {
        request
            .validate_limits(usize::MAX, usize::MAX, usize::MAX)
            .map_err(|_| StoreError::InvalidRecord)?;
        self.with_lock(|| {
            let new = Record {
                version: 1,
                run_id: id,
                origin: origin.as_url().as_str().to_owned(),
                submission: SubmissionState::Pending {
                    payload: serde_json::to_string(request)
                        .map_err(|_| StoreError::InvalidRecord)?,
                },
            };
            match self.read(id)? {
                None => {
                    self.initial(&new)?;
                    Ok(new.submission)
                }
                Some(old) if old == new => {
                    sync_directory(&self.dir)?;
                    Ok(old.submission)
                }
                Some(_) => Err(StoreError::Conflict),
            }
        })
    }
    /// Read the detached state only for the original validated origin.
    pub fn load(&self, id: Uuid, origin: &BackendOrigin) -> Result<SubmissionState, StoreError> {
        self.with_lock(|| {
            let record = self.read(id)?.ok_or(StoreError::Missing)?;
            if record.origin != origin.as_url().as_str() {
                return Err(StoreError::Conflict);
            }
            sync_directory(&self.dir)?;
            Ok(record.submission)
        })
    }
    /// Replace pending payload only after a verified backend acknowledgement.
    pub fn acknowledge(
        &self,
        id: Uuid,
        origin: &BackendOrigin,
        receipt: &RunReceipt,
    ) -> Result<SubmissionState, StoreError> {
        if receipt.run_id != id {
            return Err(StoreError::Conflict);
        }
        self.with_lock(|| {
            let mut record = self.read(id)?.ok_or(StoreError::Missing)?;
            if record.origin != origin.as_url().as_str() {
                return Err(StoreError::Conflict);
            }
            let next = SubmissionState::Acknowledged {
                receipt: receipt.clone(),
            };
            match &record.submission {
                SubmissionState::Pending { .. } => {
                    record.submission = next.clone();
                    self.replace(&record)?;
                    Ok(next)
                }
                old if *old == next => {
                    sync_directory(&self.dir)?;
                    Ok(next)
                }
                _ => Err(StoreError::Conflict),
            }
        })
    }
}

#[cfg(test)]
mod sync_tests {
    use super::*;
    use crate::wire::{Identifier, InteractionPolicy, RecoveryPolicy, RunLifecycle};
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn failed_flushes_never_authorize_success_and_can_be_retried() {
        let path = std::env::temp_dir().join(format!("council-sync-fault-{}", std::process::id()));
        fs::create_dir(&path).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        let store = ReceiptStore::open(&path).unwrap();
        let origin = BackendOrigin::parse("http://127.0.0.1:8001").unwrap();
        let id = Uuid::nil();
        let request = CreateRunRequest {
            content: "synthetic".into(),
            negative_prompt: None,
            skill_ids: vec![],
            refine: false,
            interaction_policy: InteractionPolicy::Persistent,
            recovery_policy: RecoveryPolicy::ExplicitRetry,
        };
        // File was written but its first sync fails: retry must resync it and
        // the directory before exposing pending state.
        FAIL_SYNC.store(1, Ordering::SeqCst);
        assert_eq!(store.prepare(id, &origin, &request), Err(StoreError::Io));
        FAIL_SYNC.store(2, Ordering::SeqCst);
        assert_eq!(store.load(id, &origin), Err(StoreError::Io));
        assert!(matches!(
            store.load(id, &origin).unwrap(),
            SubmissionState::Pending { .. }
        ));
        let receipt = RunReceipt {
            run_id: id,
            conversation_id: Identifier::new("synthetic".into()).unwrap(),
            assistant_index: None,
            lifecycle: RunLifecycle::Queued,
        };
        // File and temporary acknowledgement were synced but the directory
        // sync after rename failed. Retry must resync before accepting it.
        FAIL_SYNC.store(2, Ordering::SeqCst);
        assert_eq!(
            store.acknowledge(id, &origin, &receipt),
            Err(StoreError::Io)
        );
        FAIL_SYNC.store(2, Ordering::SeqCst);
        assert_eq!(store.load(id, &origin), Err(StoreError::Io));
        assert!(matches!(
            store.acknowledge(id, &origin, &receipt).unwrap(),
            SubmissionState::Acknowledged { .. }
        ));
        fs::remove_dir_all(path).unwrap();
    }
}
