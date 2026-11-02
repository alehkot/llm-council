//! One-time credential setup. Authentication succeeds before Keychain is changed.
//! This flow never submits a consultation or enables unsupported run operations.
use std::{fmt, future::Future};

use serde::Serialize;

use crate::{
    config::{BackendConfig, BackendOrigin, REQUIRED_API_VERSION},
    credentials::{validate_key, CredentialError, CredentialStore},
    http::{ConfigClient, ProbeError},
    output::terminal_text,
    terminal::{HiddenPrompt, PromptError},
};

/// A configuration read only: implementations must not submit or retry work.
pub trait ConfigVerifier {
    fn verify<'a>(
        &'a mut self,
        origin: &'a BackendOrigin,
        key: &'a str,
    ) -> impl Future<Output = Result<BackendConfig, ProbeError>> + 'a;
}

pub struct HttpConfigVerifier;

impl ConfigVerifier for HttpConfigVerifier {
    async fn verify<'a>(
        &'a mut self,
        origin: &'a BackendOrigin,
        key: &'a str,
    ) -> Result<BackendConfig, ProbeError> {
        ConfigClient::new(origin.clone(), key)?
            .probe_for_setup()
            .await
    }
}

/// Contains only non-secret configuration and truthful command capabilities.
#[derive(Debug, Serialize)]
pub struct SetupResult {
    pub backend_origin: String,
    pub backend_cli_api_version: u32,
    pub council_models: Vec<String>,
    pub chairman_model: String,
    pub credential_stored: bool,
    pub consultation_commands_available: bool,
}

impl SetupResult {
    /// Human-facing output is separate from JSON, which preserves config values.
    pub fn terminal_summary(&self) -> String {
        let mut output = format!(
            "Backend credential saved in macOS Keychain for {}.\nConfigured council:\n",
            terminal_text(&self.backend_origin)
        );
        for model in &self.council_models {
            output.push_str(&format!("  - {}\n", terminal_text(model)));
        }
        output.push_str(&format!(
            "Chairman: {}\nBackend CLI API version: {}.\nConsultation commands are not connected in this CLI yet; setup does not start model work.\n",
            terminal_text(&self.chairman_model), self.backend_cli_api_version
        ));
        if self.backend_cli_api_version == 0 {
            output.push_str(
                "The backend also lacks the durable CLI API v1 required for consultations.\n",
            );
        }
        output
    }
}

/// Errors deliberately carry no credential, arbitrary server text or OS detail.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SetupError {
    NonInteractive,
    Credential(CredentialError),
    Prompt(PromptError),
    Probe(ProbeError),
}

impl SetupError {
    pub fn code(self) -> &'static str {
        match self {
            Self::NonInteractive => "needs_input",
            Self::Credential(_) => "credential_store",
            Self::Prompt(_) => "credential_input",
            Self::Probe(_) => "backend_configuration",
        }
    }

    pub fn exit_code(self) -> u8 {
        match self {
            Self::NonInteractive | Self::Prompt(PromptError::Unavailable) => 3,
            Self::Prompt(PromptError::Interrupted) => 130,
            _ => 1,
        }
    }
}

impl fmt::Display for SetupError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NonInteractive => formatter.write_str(
                "Setup requires hidden credential entry in a controlling terminal. Run council setup without --non-interactive.",
            ),
            Self::Credential(error) => error.fmt(formatter),
            Self::Prompt(error) => error.fmt(formatter),
            Self::Probe(error) => error.fmt(formatter),
        }
    }
}
impl std::error::Error for SetupError {}

/// Check platform/input availability before requesting a secret; authenticate
/// exactly once, then store. Any probe failure leaves the prior entry untouched.
/// API0 can authenticate setup, but cannot authorize legacy run submission.
pub async fn run_setup(
    origin: &BackendOrigin,
    non_interactive: bool,
    store: &mut impl CredentialStore,
    prompt: &mut impl HiddenPrompt,
    verifier: &mut impl ConfigVerifier,
) -> Result<SetupResult, SetupError> {
    if non_interactive {
        return Err(SetupError::NonInteractive);
    }
    store.available().map_err(SetupError::Credential)?;
    let key = prompt.read_key().map_err(SetupError::Prompt)?;
    // Reject invalid input even for injected verifiers, without a network call.
    validate_key(&key).map_err(|_| SetupError::Prompt(PromptError::InvalidCredential))?;
    let config = verifier
        .verify(origin, &key)
        .await
        .map_err(SetupError::Probe)?;
    // A successful HTTP exchange is not enough: refuse incomplete or future
    // configurations before replacing a known-good credential.
    if config.cli_api_version > REQUIRED_API_VERSION {
        return Err(SetupError::Probe(ProbeError::UnsupportedSetupVersion));
    }
    if config.prompt_max_chars == 0
        || config.negative_prompt_max_chars == 0
        || config.skills_max_selected == 0
    {
        return Err(SetupError::Probe(ProbeError::InvalidLimits));
    }
    if config.council_models.is_empty()
        || config
            .council_models
            .iter()
            .any(|model| model.trim().is_empty())
        || config.chairman_model.trim().is_empty()
    {
        return Err(SetupError::Probe(ProbeError::InvalidResponse));
    }
    store.store(origin, &key).map_err(SetupError::Credential)?;
    Ok(SetupResult {
        backend_origin: origin.as_url().as_str().to_owned(),
        backend_cli_api_version: config.cli_api_version,
        council_models: config.council_models,
        chairman_model: config.chairman_model,
        credential_stored: true,
        // The binary's run dispatch remains unimplemented, even with an API1 server.
        consultation_commands_available: false,
    })
}

#[cfg(test)]
mod tests {
    use std::{cell::RefCell, rc::Rc};

    use super::*;

    const SYNTHETIC_KEY: &str = "synthetic-test-only-new-key";
    type Events = Rc<RefCell<Vec<&'static str>>>;

    struct Store {
        events: Events,
        availability: Result<(), CredentialError>,
        save_result: Result<(), CredentialError>,
        saved: String,
        origin: Option<String>,
    }
    impl CredentialStore for Store {
        fn available(&self) -> Result<(), CredentialError> {
            self.events.borrow_mut().push("available");
            self.availability
        }
        fn read(&self, _: &BackendOrigin) -> Result<Option<String>, CredentialError> {
            panic!("setup must not read or print the old credential")
        }
        fn store(&mut self, origin: &BackendOrigin, key: &str) -> Result<(), CredentialError> {
            self.events.borrow_mut().push("store");
            self.save_result?;
            self.saved = key.to_owned();
            self.origin = Some(origin.as_url().as_str().to_owned());
            Ok(())
        }
    }

    struct Prompt {
        events: Events,
        result: Result<String, PromptError>,
    }
    impl HiddenPrompt for Prompt {
        fn read_key(&mut self) -> Result<String, PromptError> {
            self.events.borrow_mut().push("prompt");
            self.result.clone()
        }
    }

    struct Verifier {
        events: Events,
        error: Option<ProbeError>,
        api_version: u32,
        amend_config: Option<fn(&mut BackendConfig)>,
    }
    impl ConfigVerifier for Verifier {
        async fn verify<'a>(
            &'a mut self,
            origin: &'a BackendOrigin,
            key: &'a str,
        ) -> Result<BackendConfig, ProbeError> {
            self.events.borrow_mut().push("verify");
            assert_eq!(origin.as_url().as_str(), "http://127.0.0.1:8001/");
            assert_eq!(key, SYNTHETIC_KEY);
            if let Some(error) = self.error {
                return Err(error);
            }
            let mut config = BackendConfig {
                cli_api_version: self.api_version,
                prompt_max_chars: 100,
                negative_prompt_max_chars: 100,
                skills_max_selected: 3,
                council_models: vec!["test/member".to_owned()],
                chairman_model: "test/chairman".to_owned(),
            };
            if let Some(amend) = self.amend_config {
                amend(&mut config);
            }
            Ok(config)
        }
    }

    fn fixture() -> (BackendOrigin, Events, Store, Prompt, Verifier) {
        let events = Rc::new(RefCell::new(Vec::new()));
        (
            BackendOrigin::parse("http://localhost:8001").unwrap(),
            events.clone(),
            Store {
                events: events.clone(),
                availability: Ok(()),
                save_result: Ok(()),
                saved: "synthetic-old-key".to_owned(),
                origin: None,
            },
            Prompt {
                events: events.clone(),
                result: Ok(SYNTHETIC_KEY.to_owned()),
            },
            Verifier {
                events,
                error: None,
                api_version: 0,
                amend_config: None,
            },
        )
    }

    #[tokio::test]
    async fn successful_setup_is_ordered_origin_bound_and_has_no_key_in_output() {
        for version in [0, 1] {
            let (origin, events, mut store, mut prompt, mut verifier) = fixture();
            verifier.api_version = version;
            let result = run_setup(&origin, false, &mut store, &mut prompt, &mut verifier)
                .await
                .unwrap();
            assert_eq!(*events.borrow(), ["available", "prompt", "verify", "store"]);
            assert_eq!(store.saved, SYNTHETIC_KEY);
            assert_eq!(store.origin.as_deref(), Some("http://127.0.0.1:8001/"));
            assert!(result.credential_stored);
            assert!(!result.consultation_commands_available);
            assert_eq!(result.backend_cli_api_version, version);
            let json = serde_json::to_string(&result).unwrap();
            assert!(!json.contains(SYNTHETIC_KEY));
            assert!(!result.terminal_summary().contains(SYNTHETIC_KEY));
            assert!(result.terminal_summary().contains("not connected"));
            assert_eq!(
                result.terminal_summary().contains("also lacks"),
                version == 0
            );
        }
    }

    #[tokio::test]
    async fn real_config_transport_authenticates_before_mock_keychain_storage() {
        use tokio::{
            io::{AsyncReadExt, AsyncWriteExt},
            net::TcpListener,
        };
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let origin =
            BackendOrigin::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            while !request.ends_with(b"\r\n\r\n") {
                let mut byte = [0_u8; 1];
                assert_eq!(stream.read(&mut byte).await.unwrap(), 1);
                request.push(byte[0]);
                assert!(request.len() < 8192);
            }
            let request = String::from_utf8(request).unwrap();
            assert!(request.starts_with("GET /api/config HTTP/1.1\r\n"));
            assert!(request
                .to_ascii_lowercase()
                .contains(&format!("x-api-key: {SYNTHETIC_KEY}\r\n")));
            let body = r#"{"cli_api_version":0,"prompt_max_chars":100,"negative_prompt_max_chars":100,"skills_max_selected":3,"council_models":["test/member"],"chairman_model":"test/chairman"}"#;
            stream.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).as_bytes()).await.unwrap();
        });
        let (_, events, mut store, mut prompt, _) = fixture();
        let result = run_setup(
            &origin,
            false,
            &mut store,
            &mut prompt,
            &mut HttpConfigVerifier,
        )
        .await
        .unwrap();
        server.await.unwrap();
        assert_eq!(*events.borrow(), ["available", "prompt", "store"]);
        assert_eq!(store.saved, SYNTHETIC_KEY);
        assert_eq!(store.origin.as_deref(), Some(origin.as_url().as_str()));
        assert_eq!(result.council_models, ["test/member"]);
        assert!(result.credential_stored);
        assert!(!result.consultation_commands_available);
    }

    #[tokio::test]
    async fn noninteractive_never_touches_keychain_terminal_or_network() {
        let (origin, events, mut store, mut prompt, mut verifier) = fixture();
        let error = run_setup(&origin, true, &mut store, &mut prompt, &mut verifier)
            .await
            .unwrap_err();
        assert_eq!(error, SetupError::NonInteractive);
        assert_eq!(error.exit_code(), 3);
        assert!(events.borrow().is_empty());
    }

    #[tokio::test]
    async fn unavailable_keychain_is_detected_before_prompt() {
        let (origin, events, mut store, mut prompt, mut verifier) = fixture();
        store.availability = Err(CredentialError::UnsupportedPlatform);
        assert_eq!(
            run_setup(&origin, false, &mut store, &mut prompt, &mut verifier)
                .await
                .unwrap_err(),
            SetupError::Credential(CredentialError::UnsupportedPlatform)
        );
        assert_eq!(*events.borrow(), ["available"]);
    }

    #[tokio::test]
    async fn missing_terminal_or_cancel_never_probes_or_stores() {
        for error in [
            PromptError::Unavailable,
            PromptError::Interrupted,
            PromptError::InvalidCredential,
        ] {
            let (origin, events, mut store, mut prompt, mut verifier) = fixture();
            prompt.result = Err(error);
            assert_eq!(
                run_setup(&origin, false, &mut store, &mut prompt, &mut verifier)
                    .await
                    .unwrap_err(),
                SetupError::Prompt(error)
            );
            assert_eq!(*events.borrow(), ["available", "prompt"]);
            assert_eq!(store.saved, "synthetic-old-key");
        }
    }

    #[tokio::test]
    async fn invalid_key_is_not_sent_or_stored() {
        let oversized = "x".repeat(crate::credentials::MAX_KEY_BYTES + 1);
        for key in [
            "",
            "with space",
            "newline\n",
            "\u{7f}",
            "é",
            oversized.as_str(),
        ] {
            let (origin, events, mut store, mut prompt, mut verifier) = fixture();
            prompt.result = Ok(key.to_owned());
            assert_eq!(
                run_setup(&origin, false, &mut store, &mut prompt, &mut verifier)
                    .await
                    .unwrap_err(),
                SetupError::Prompt(PromptError::InvalidCredential)
            );
            assert_eq!(*events.borrow(), ["available", "prompt"]);
            assert_eq!(store.saved, "synthetic-old-key");
        }
    }

    #[tokio::test]
    async fn every_probe_failure_preserves_prior_credential_without_retry() {
        for error in [
            ProbeError::Authentication,
            ProbeError::LockedOut,
            ProbeError::Connection,
            ProbeError::Timeout,
            ProbeError::Redirect,
            ProbeError::HttpStatus(500),
            ProbeError::ResponseTooLarge,
            ProbeError::InvalidResponse,
            ProbeError::UnsupportedSetupVersion,
            ProbeError::InvalidLimits,
        ] {
            let (origin, events, mut store, mut prompt, mut verifier) = fixture();
            verifier.error = Some(error);
            let actual = run_setup(&origin, false, &mut store, &mut prompt, &mut verifier)
                .await
                .unwrap_err();
            assert_eq!(actual, SetupError::Probe(error));
            assert_eq!(*events.borrow(), ["available", "prompt", "verify"]);
            assert_eq!(store.saved, "synthetic-old-key");
            assert!(!format!("{actual:?} {actual}").contains(SYNTHETIC_KEY));
        }
    }

    #[tokio::test]
    async fn incomplete_config_cannot_overwrite_a_saved_credential() {
        type Mutation = (fn(&mut BackendConfig), ProbeError);
        let mutations: [Mutation; 7] = [
            (
                |config| config.cli_api_version = 2,
                ProbeError::UnsupportedSetupVersion,
            ),
            (
                |config| config.prompt_max_chars = 0,
                ProbeError::InvalidLimits,
            ),
            (
                |config| config.negative_prompt_max_chars = 0,
                ProbeError::InvalidLimits,
            ),
            (
                |config| config.skills_max_selected = 0,
                ProbeError::InvalidLimits,
            ),
            (
                |config| config.council_models.clear(),
                ProbeError::InvalidResponse,
            ),
            (
                |config| config.council_models.push("  ".to_owned()),
                ProbeError::InvalidResponse,
            ),
            (
                |config| config.chairman_model = " ".to_owned(),
                ProbeError::InvalidResponse,
            ),
        ];
        for (amend, expected) in mutations {
            let (origin, events, mut store, mut prompt, mut verifier) = fixture();
            verifier.amend_config = Some(amend);
            assert_eq!(
                run_setup(&origin, false, &mut store, &mut prompt, &mut verifier)
                    .await
                    .unwrap_err(),
                SetupError::Probe(expected)
            );
            assert_eq!(*events.borrow(), ["available", "prompt", "verify"]);
            assert_eq!(store.saved, "synthetic-old-key");
        }
    }

    #[tokio::test]
    async fn storage_failure_cannot_report_success() {
        let (origin, events, mut store, mut prompt, mut verifier) = fixture();
        store.save_result = Err(CredentialError::AccessDenied);
        assert_eq!(
            run_setup(&origin, false, &mut store, &mut prompt, &mut verifier)
                .await
                .unwrap_err(),
            SetupError::Credential(CredentialError::AccessDenied)
        );
        assert_eq!(*events.borrow(), ["available", "prompt", "verify", "store"]);
        assert_eq!(store.saved, "synthetic-old-key");
    }

    #[test]
    fn setup_errors_have_machine_readable_input_failure_codes() {
        assert_eq!(SetupError::NonInteractive.code(), "needs_input");
        assert_eq!(SetupError::Prompt(PromptError::Unavailable).exit_code(), 3);
        assert_eq!(
            SetupError::Prompt(PromptError::Interrupted).exit_code(),
            130
        );
        assert_eq!(
            SetupError::Credential(CredentialError::AccessDenied).exit_code(),
            1
        );
        assert_eq!(SetupError::Probe(ProbeError::Authentication).exit_code(), 1);
    }

    #[test]
    fn untrusted_models_are_escaped_only_for_terminal_display() {
        let result = SetupResult {
            backend_origin: "http://127.0.0.1:8001/".to_owned(),
            backend_cli_api_version: 0,
            council_models: vec!["member\u{1b}[2J\r".to_owned()],
            chairman_model: "chair\u{202e}hidden".to_owned(),
            credential_stored: true,
            consultation_commands_available: false,
        };
        let display = result.terminal_summary();
        assert!(!display.contains('\u{1b}'));
        assert!(!display.contains('\r'));
        assert!(!display.contains('\u{202e}'));
        let json: serde_json::Value = serde_json::to_value(&result).unwrap();
        assert_eq!(json["council_models"][0], "member\u{1b}[2J\r");
    }
}
