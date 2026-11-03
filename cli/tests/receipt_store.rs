#![cfg(unix)]
use council_cli::config::BackendOrigin;
use council_cli::receipt_store::{ReceiptStore, StoreError, SubmissionState};
use council_cli::wire::{
    CreateRunRequest, Identifier, InteractionPolicy, RecoveryPolicy, RunLifecycle, RunReceipt,
};
use std::fs::{self, OpenOptions};
use std::os::unix::fs::{symlink, OpenOptionsExt, PermissionsExt};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Barrier, Mutex, MutexGuard};
use uuid::Uuid;

static COUNTER: AtomicU64 = AtomicU64::new(0);
// A separate-process lock check can momentarily inherit unrelated test file
// descriptors at fork. Serialize test directories, not production operations.
static TEST_SERIAL: Mutex<()> = Mutex::new(());
struct Temp(PathBuf, #[allow(dead_code)] MutexGuard<'static, ()>);
impl Temp {
    fn new() -> Self {
        let guard = TEST_SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        let path = std::env::temp_dir().join(format!(
            "council-receipt-test-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        Self(path, guard)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn id() -> Uuid {
    Uuid::parse_str("01234567-89ab-4def-8123-456789abcdef").unwrap()
}
fn origin() -> BackendOrigin {
    BackendOrigin::parse("http://localhost:8001").unwrap()
}
fn request() -> CreateRunRequest {
    CreateRunRequest {
        content: "Exact\nПривет 🦀\u{0000}".into(),
        negative_prompt: Some("do not change\n".into()),
        skill_ids: vec![Identifier::new("s-1".into()).unwrap()],
        refine: true,
        interaction_policy: InteractionPolicy::Persistent,
        recovery_policy: RecoveryPolicy::ExplicitRetry,
    }
}
fn receipt() -> RunReceipt {
    RunReceipt {
        run_id: id(),
        conversation_id: Identifier::new("conversation-1".into()).unwrap(),
        assistant_index: Some(1),
        lifecycle: RunLifecycle::Queued,
    }
}

#[test]
fn pending_exact_content_survives_reopen_and_acknowledgement_removes_it() {
    let t = Temp::new();
    let store = ReceiptStore::open(&t.0).unwrap();
    let r = request();
    let pending = SubmissionState::Pending {
        payload: serde_json::to_string(&r).unwrap(),
    };
    assert_eq!(store.prepare(id(), &origin(), &r).unwrap(), pending);
    assert_eq!(store.prepare(id(), &origin(), &r).unwrap(), pending);
    drop(store);
    let store = ReceiptStore::open(&t.0).unwrap();
    assert_eq!(store.load(id(), &origin()).unwrap(), pending);
    let acknowledged = SubmissionState::Acknowledged { receipt: receipt() };
    assert_eq!(
        store.acknowledge(id(), &origin(), &receipt()).unwrap(),
        acknowledged
    );
    assert_eq!(
        store.acknowledge(id(), &origin(), &receipt()).unwrap(),
        acknowledged
    );
    assert_eq!(store.load(id(), &origin()).unwrap(), acknowledged);
    assert_eq!(
        store.prepare(id(), &origin(), &r),
        Err(StoreError::Conflict)
    );
    let bytes = fs::read_to_string(t.0.join(format!("{}.json", id()))).unwrap();
    assert!(!bytes.contains("Привет"));
    assert!(!bytes.contains("negative_prompt"));
    assert_eq!(
        fs::metadata(t.0.join(format!("{}.json", id())))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
}

#[test]
fn conflicts_and_missing_receipts_fail_closed() {
    let t = Temp::new();
    let s = ReceiptStore::open(&t.0).unwrap();
    let mut other = request();
    other.content = "different".into();
    assert_eq!(s.load(id(), &origin()), Err(StoreError::Missing));
    assert_eq!(
        s.acknowledge(id(), &origin(), &receipt()),
        Err(StoreError::Missing)
    );
    s.prepare(id(), &origin(), &request()).unwrap();
    assert_eq!(
        s.prepare(id(), &origin(), &other),
        Err(StoreError::Conflict)
    );
    let another = BackendOrigin::parse("http://127.0.0.1:8002").unwrap();
    assert_eq!(s.load(id(), &another), Err(StoreError::Conflict));
    let mut wrong = receipt();
    wrong.run_id = Uuid::nil();
    assert_eq!(
        s.acknowledge(id(), &origin(), &wrong),
        Err(StoreError::Conflict)
    );
    s.acknowledge(id(), &origin(), &receipt()).unwrap();
    let mut changed = receipt();
    changed.assistant_index = Some(2);
    assert_eq!(
        s.acknowledge(id(), &origin(), &changed),
        Err(StoreError::Conflict)
    );
}

#[test]
fn unsafe_permissions_symlinks_and_hardlinks_are_rejected() {
    let t = Temp::new();
    fs::set_permissions(&t.0, fs::Permissions::from_mode(0o755)).unwrap();
    assert!(matches!(
        ReceiptStore::open(&t.0),
        Err(StoreError::UnsafeDirectory)
    ));
    fs::set_permissions(&t.0, fs::Permissions::from_mode(0o700)).unwrap();
    let link = t.0.join("root-link");
    symlink(&t.0, &link).unwrap();
    assert!(matches!(
        ReceiptStore::open(&link),
        Err(StoreError::UnsafeDirectory)
    ));
    let s = ReceiptStore::open(&t.0).unwrap();
    let target = t.0.join("outside");
    fs::write(&target, "untouched").unwrap();
    let record = t.0.join(format!("{}.json", id()));
    symlink(&target, &record).unwrap();
    assert!(s.prepare(id(), &origin(), &request()).is_err());
    assert_eq!(fs::read_to_string(&target).unwrap(), "untouched");
    fs::remove_file(&record).unwrap();
    fs::set_permissions(&target, fs::Permissions::from_mode(0o600)).unwrap();
    fs::hard_link(&target, &record).unwrap();
    assert_eq!(s.load(id(), &origin()), Err(StoreError::UnsafeFile));
}

#[test]
fn malformed_records_do_not_get_overwritten() {
    let t = Temp::new();
    let s = ReceiptStore::open(&t.0).unwrap();
    let path = t.0.join(format!("{}.json", id()));
    fs::write(&path, b"{partial").unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
    assert_eq!(
        s.prepare(id(), &origin(), &request()),
        Err(StoreError::InvalidRecord)
    );
    assert_eq!(fs::read(&path).unwrap(), b"{partial");
}

#[test]
fn simultaneous_writers_do_not_replace_the_first_payload() {
    let t = Temp::new();
    let barrier = Arc::new(Barrier::new(12));
    let mut handles = Vec::new();
    for n in 0..12 {
        let path = t.0.clone();
        let b = barrier.clone();
        handles.push(std::thread::spawn(move || {
            let s = ReceiptStore::open(&path).unwrap();
            let mut r = request();
            r.content = format!("payload-{n}");
            b.wait();
            s.prepare(id(), &origin(), &r)
        }));
    }
    let outcomes: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    assert_eq!(outcomes.iter().filter(|v| v.is_ok()).count(), 1);
    assert!(outcomes
        .iter()
        .all(|r| r.is_ok() || matches!(r, Err(StoreError::Busy | StoreError::Conflict))));
    assert!(matches!(
        ReceiptStore::open(&t.0)
            .unwrap()
            .load(id(), &origin())
            .unwrap(),
        SubmissionState::Pending { .. }
    ));
}

#[test]
fn lock_is_process_shared_and_nonblocking() {
    let t = Temp::new();
    let s = ReceiptStore::open(&t.0).unwrap();
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(t.0.join(".lock"))
        .unwrap();
    lock.try_lock().unwrap();
    assert_eq!(
        s.prepare(id(), &origin(), &request()),
        Err(StoreError::Busy)
    );
}

#[test]
fn payload_is_immutable_wire_bytes_and_debug_is_redacted() {
    let t = Temp::new();
    let s = ReceiptStore::open(&t.0).unwrap();
    let r = request();
    let state = s.prepare(id(), &origin(), &r).unwrap();
    let SubmissionState::Pending { payload } = &state else {
        panic!("expected pending")
    };
    assert_eq!(payload.as_bytes(), serde_json::to_vec(&r).unwrap());
    let value: CreateRunRequest = serde_json::from_str(payload).unwrap();
    assert_eq!(value, r);
    assert!(!format!("{state:?}").contains("Привет"));
    assert!(!format!("{state:?}").contains("do not change"));
}

#[test]
fn replacing_path_does_not_redirect_open_store() {
    let parent = Temp::new();
    let root = parent.0.join("root");
    let moved = parent.0.join("moved");
    let elsewhere = parent.0.join("elsewhere");
    fs::create_dir(&root).unwrap();
    fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();
    fs::create_dir(&elsewhere).unwrap();
    fs::set_permissions(&elsewhere, fs::Permissions::from_mode(0o700)).unwrap();
    let s = ReceiptStore::open(&root).unwrap();
    fs::rename(&root, &moved).unwrap();
    symlink(&elsewhere, &root).unwrap();
    s.prepare(id(), &origin(), &request()).unwrap();
    assert!(moved.join(format!("{}.json", id())).is_file());
    assert!(!elsewhere.join(format!("{}.json", id())).exists());
}

#[test]
fn unacknowledged_request_survives_unrelated_acknowledgement_temporary_file() {
    let t = Temp::new();
    let s = ReceiptStore::open(&t.0).unwrap();
    let pending = s.prepare(id(), &origin(), &request()).unwrap();
    fs::write(t.0.join(".ack-999-999"), b"partial").unwrap();
    assert_eq!(s.load(id(), &origin()).unwrap(), pending);
    assert_eq!(fs::read(t.0.join(".ack-999-999")).unwrap(), b"partial");
}

#[test]
fn child_lock_check() {
    let Some(path) = std::env::var_os("COUNCIL_TEST_RECEIPT_DIR") else {
        return;
    };
    let s = ReceiptStore::open(std::path::Path::new(&path)).unwrap();
    assert_eq!(
        s.prepare(id(), &origin(), &request()),
        Err(StoreError::Busy)
    );
}

#[test]
fn lock_prevents_a_separate_process_from_writing() {
    let t = Temp::new();
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(t.0.join(".lock"))
        .unwrap();
    file.try_lock().unwrap();
    let output = std::process::Command::new(std::env::current_exe().unwrap())
        .arg("--exact")
        .arg("child_lock_check")
        .arg("--nocapture")
        .env("COUNCIL_TEST_RECEIPT_DIR", &t.0)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "child lock test failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(!t.0.join(format!("{}.json", id())).exists());
}
