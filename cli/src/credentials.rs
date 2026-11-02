//! Origin-scoped macOS Keychain access. No plaintext or command-line fallback.
use std::fmt;

use crate::config::BackendOrigin;

pub const MAX_KEY_BYTES: usize = 4096;
#[cfg(any(target_os = "macos", test))]
const KEYCHAIN_SERVICE: &str = "org.llm-council.cli.backend";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CredentialError {
    UnsupportedPlatform,
    AccessDenied,
    InvalidCredential,
}

impl fmt::Display for CredentialError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::UnsupportedPlatform => "Backend credential storage requires macOS Keychain; no plaintext fallback is available.",
            Self::AccessDenied => "Could not access macOS Keychain. Unlock it or allow access, then try again.",
            Self::InvalidCredential => "The backend key must contain 1–4096 printable ASCII characters without spaces.",
        })
    }
}
impl std::error::Error for CredentialError {}

/// Same printable-header policy as the HTTP transport, with an input size limit.
/// Do not trim: silently changing a credential can authenticate a different key.
pub fn validate_key(key: &str) -> Result<(), CredentialError> {
    if key.is_empty()
        || key.len() > MAX_KEY_BYTES
        || key.bytes().any(|byte| !(0x21..=0x7e).contains(&byte))
    {
        Err(CredentialError::InvalidCredential)
    } else {
        Ok(())
    }
}

pub trait CredentialStore {
    /// Checks platform support only, not whether the user's Keychain is unlocked.
    fn available(&self) -> Result<(), CredentialError>;
    fn read(&self, origin: &BackendOrigin) -> Result<Option<String>, CredentialError>;
    /// Call only after backend verification. Updates without deleting the old entry first.
    fn store(&mut self, origin: &BackendOrigin, key: &str) -> Result<(), CredentialError>;
}

#[derive(Debug, Default)]
pub struct SystemCredentialStore;

#[cfg(any(target_os = "macos", test))]
fn account(origin: &BackendOrigin) -> String {
    origin.as_url().origin().ascii_serialization()
}

// The tiny native boundary is injectable so tests never touch a user's Keychain.
#[cfg(any(target_os = "macos", test))]
trait KeychainAccess {
    fn get(&self, service: &str, account: &str) -> Result<Option<Vec<u8>>, CredentialError>;
    fn set(&mut self, service: &str, account: &str, key: &[u8]) -> Result<(), CredentialError>;
}

#[cfg(any(target_os = "macos", test))]
fn read_from(
    access: &impl KeychainAccess,
    origin: &BackendOrigin,
) -> Result<Option<String>, CredentialError> {
    access
        .get(KEYCHAIN_SERVICE, &account(origin))?
        .map(|bytes| {
            let key = String::from_utf8(bytes).map_err(|_| CredentialError::InvalidCredential)?;
            validate_key(&key)?;
            Ok(key)
        })
        .transpose()
}

#[cfg(any(target_os = "macos", test))]
fn store_in(
    access: &mut impl KeychainAccess,
    origin: &BackendOrigin,
    key: &str,
) -> Result<(), CredentialError> {
    validate_key(key)?;
    access.set(KEYCHAIN_SERVICE, &account(origin), key.as_bytes())
}

#[cfg(target_os = "macos")]
struct NativeKeychain;

#[cfg(target_os = "macos")]
impl KeychainAccess for NativeKeychain {
    fn get(&self, service: &str, account: &str) -> Result<Option<Vec<u8>>, CredentialError> {
        use security_framework::passwords::{generic_password, PasswordOptions};
        // Apple's errSecItemNotFound OSStatus. Other OS errors never reach output.
        const ITEM_NOT_FOUND: i32 = -25300;
        match generic_password(PasswordOptions::new_generic_password(service, account)) {
            Ok(bytes) => Ok(Some(bytes)),
            Err(error) if error.code() == ITEM_NOT_FOUND => Ok(None),
            Err(_) => Err(CredentialError::AccessDenied),
        }
    }

    fn set(&mut self, service: &str, account: &str, key: &[u8]) -> Result<(), CredentialError> {
        security_framework::passwords::set_generic_password(service, account, key)
            .map_err(|_| CredentialError::AccessDenied)
    }
}

impl CredentialStore for SystemCredentialStore {
    fn available(&self) -> Result<(), CredentialError> {
        if cfg!(target_os = "macos") {
            Ok(())
        } else {
            Err(CredentialError::UnsupportedPlatform)
        }
    }

    fn read(&self, origin: &BackendOrigin) -> Result<Option<String>, CredentialError> {
        #[cfg(target_os = "macos")]
        {
            read_from(&NativeKeychain, origin)
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = origin;
            Err(CredentialError::UnsupportedPlatform)
        }
    }

    fn store(&mut self, origin: &BackendOrigin, key: &str) -> Result<(), CredentialError> {
        #[cfg(target_os = "macos")]
        {
            store_in(&mut NativeKeychain, origin, key)
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = (origin, key);
            Err(CredentialError::UnsupportedPlatform)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::HashMap;

    #[derive(Default)]
    struct FakeKeychain {
        entries: RefCell<HashMap<(String, String), Vec<u8>>>,
        deny: bool,
    }
    impl KeychainAccess for FakeKeychain {
        fn get(&self, service: &str, account: &str) -> Result<Option<Vec<u8>>, CredentialError> {
            if self.deny {
                return Err(CredentialError::AccessDenied);
            }
            Ok(self
                .entries
                .borrow()
                .get(&(service.into(), account.into()))
                .cloned())
        }
        fn set(&mut self, service: &str, account: &str, key: &[u8]) -> Result<(), CredentialError> {
            if self.deny {
                return Err(CredentialError::AccessDenied);
            }
            self.entries
                .borrow_mut()
                .insert((service.into(), account.into()), key.into());
            Ok(())
        }
    }
    fn origin(raw: &str) -> BackendOrigin {
        BackendOrigin::parse(raw).unwrap()
    }

    #[test]
    fn normalized_origins_share_account_but_scheme_port_and_host_do_not() {
        let mut fake = FakeKeychain::default();
        let canonical = origin("http://127.0.0.1:8001");
        store_in(&mut fake, &origin("http://localhost:8001/"), "test-key").unwrap();
        assert_eq!(
            read_from(&fake, &canonical).unwrap().as_deref(),
            Some("test-key")
        );
        for other in [
            "https://127.0.0.1:8001",
            "http://127.0.0.1:8002",
            "http://[::1]:8001",
        ] {
            assert_eq!(read_from(&fake, &origin(other)).unwrap(), None);
        }
        assert_eq!(
            account(&origin("http://localhost:80")),
            account(&origin("http://127.0.0.1"))
        );
    }

    #[test]
    fn invalid_or_denied_replacement_preserves_previous_key() {
        let mut fake = FakeKeychain::default();
        let origin = origin("http://localhost:8001");
        store_in(&mut fake, &origin, "old-test-key").unwrap();
        assert_eq!(
            store_in(&mut fake, &origin, "bad key"),
            Err(CredentialError::InvalidCredential)
        );
        fake.deny = true;
        assert_eq!(
            store_in(&mut fake, &origin, "new-test-key"),
            Err(CredentialError::AccessDenied)
        );
        fake.deny = false;
        assert_eq!(
            read_from(&fake, &origin).unwrap().as_deref(),
            Some("old-test-key")
        );
    }

    #[test]
    fn missing_is_distinct_from_access_failure_and_corrupt_data() {
        let mut fake = FakeKeychain::default();
        let origin = origin("http://localhost:8001");
        assert_eq!(read_from(&fake, &origin).unwrap(), None);
        fake.deny = true;
        assert_eq!(
            read_from(&fake, &origin),
            Err(CredentialError::AccessDenied)
        );
        fake.deny = false;
        fake.set(KEYCHAIN_SERVICE, &account(&origin), &[0xff])
            .unwrap();
        assert_eq!(
            read_from(&fake, &origin),
            Err(CredentialError::InvalidCredential)
        );
    }

    #[test]
    fn key_validation_is_bounded_and_never_trims() {
        for invalid in ["", " leading", "trailing ", "line\nfeed", "é", "\u{7f}"] {
            assert_eq!(
                validate_key(invalid),
                Err(CredentialError::InvalidCredential)
            );
        }
        assert!(validate_key(&"x".repeat(MAX_KEY_BYTES)).is_ok());
        assert!(validate_key(&"x".repeat(MAX_KEY_BYTES + 1)).is_err());
        assert!(validate_key("abc-123_!~").is_ok());
    }

    #[cfg(not(target_os = "macos"))]
    #[test]
    fn unsupported_platform_never_falls_back_to_files() {
        let mut store = SystemCredentialStore;
        let origin = origin("http://localhost:8001");
        assert_eq!(store.available(), Err(CredentialError::UnsupportedPlatform));
        assert_eq!(
            store.read(&origin),
            Err(CredentialError::UnsupportedPlatform)
        );
        assert_eq!(
            store.store(&origin, "test-key"),
            Err(CredentialError::UnsupportedPlatform)
        );
    }
}
