//! Read installed summaries using the existing API; never submit model work.
use std::{collections::HashSet, fmt};

use serde::{Deserialize, Serialize};

use crate::{
    config::BackendOrigin,
    credentials::{validate_key, CredentialError, CredentialStore},
    http::{ConfigClient, ProbeError},
    output::terminal_text,
};

/// Only summary fields needed by this command. Extra backend metadata is ignored.
#[derive(Debug, Deserialize, Serialize)]
pub struct SkillSummary {
    pub id: String,
    pub name: String,
    pub description: String,
    pub source: String,
    pub version: u64,
    pub enabled: bool,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct SkillCatalog {
    pub skills: Vec<SkillSummary>,
}

impl SkillCatalog {
    pub(crate) fn decode(body: &[u8]) -> Result<Self, ()> {
        let catalog: Self = serde_json::from_slice(body).map_err(|_| ())?;
        let mut ids = HashSet::new();
        for skill in &catalog.skills {
            if skill.id.trim().is_empty()
                || skill.name.trim().is_empty()
                || skill.source.trim().is_empty()
                || !ids.insert(&skill.id)
            {
                return Err(());
            }
        }
        Ok(catalog)
    }

    pub fn terminal_summary(&self) -> String {
        if self.skills.is_empty() {
            return "No installed skills were returned by the backend.\n".to_owned();
        }
        let mut result =
            String::from("Installed skills (consultation commands are not connected yet):\n");
        for skill in &self.skills {
            result.push_str(&format!(
                "  {} [{}; {}; v{}] — {}\n    {}\n",
                terminal_text(&skill.id),
                if skill.enabled { "enabled" } else { "disabled" },
                terminal_text(&skill.source),
                skill.version,
                terminal_text(&skill.name),
                terminal_text(&skill.description),
            ));
        }
        result
    }
}

/// Deliberately carries no credential, server body, or native Keychain detail.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkillsError {
    Credential(CredentialError),
    SetupRequired,
    Unavailable,
    Backend(ProbeError),
}

impl SkillsError {
    pub fn code(self) -> &'static str {
        match self {
            Self::Credential(_) => "credential_store",
            Self::SetupRequired => "setup_required",
            Self::Unavailable => "skills_unavailable",
            Self::Backend(_) => "backend_read",
        }
    }

    pub fn exit_code(self) -> u8 {
        if self == Self::SetupRequired {
            2
        } else {
            1
        }
    }
}

impl fmt::Display for SkillsError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Credential(error) => error.fmt(formatter),
            Self::SetupRequired => formatter.write_str("No backend key is saved for this origin. Run council setup for the same --backend-url first."),
            Self::Unavailable => formatter.write_str("The backend skill-list endpoint is unavailable. Check that skills are enabled and the backend supports GET /api/skills."),
            Self::Backend(error) => error.fmt(formatter),
        }
    }
}
impl std::error::Error for SkillsError {}

/// Read an existing origin-bound key; never prompt for or save a credential.
/// Probe authentication once, then fetch summaries once. Errors are not retried.
pub async fn run_skills(
    origin: &BackendOrigin,
    store: &impl CredentialStore,
) -> Result<SkillCatalog, SkillsError> {
    store.available().map_err(SkillsError::Credential)?;
    let key = store
        .read(origin)
        .map_err(SkillsError::Credential)?
        .ok_or(SkillsError::SetupRequired)?;
    validate_key(&key).map_err(SkillsError::Credential)?;
    let mut client = ConfigClient::new(origin.clone(), &key).map_err(SkillsError::Backend)?;
    client
        .probe_for_read_only()
        .await
        .map_err(SkillsError::Backend)?;
    client.list_skills().await.map_err(|error| match error {
        ProbeError::HttpStatus(404) => SkillsError::Unavailable,
        other => SkillsError::Backend(other),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{cell::RefCell, time::Duration};
    use tokio::{
        io::{AsyncReadExt, AsyncWriteExt},
        net::TcpListener,
    };

    const KEY: &str = "synthetic-skills-key-never-echo";
    const CONFIG: &str = r#"{"cli_api_version":0,"prompt_max_chars":100,"negative_prompt_max_chars":100,"skills_max_selected":5}"#;
    const CATALOG: &str = include_str!("../../tests/fixtures/cli/skills-list.json");

    struct Store {
        origin: BackendOrigin,
        key: Result<Option<String>, CredentialError>,
        available: Result<(), CredentialError>,
        events: RefCell<Vec<&'static str>>,
    }
    impl Store {
        fn valid(origin: BackendOrigin) -> Self {
            Self {
                origin,
                key: Ok(Some(KEY.into())),
                available: Ok(()),
                events: RefCell::new(Vec::new()),
            }
        }
    }
    impl CredentialStore for Store {
        fn available(&self) -> Result<(), CredentialError> {
            self.events.borrow_mut().push("available");
            self.available
        }
        fn read(&self, origin: &BackendOrigin) -> Result<Option<String>, CredentialError> {
            self.events.borrow_mut().push("read");
            assert_eq!(origin, &self.origin);
            self.key.clone()
        }
        fn store(&mut self, _: &BackendOrigin, _: &str) -> Result<(), CredentialError> {
            panic!("listing must never save a credential");
        }
    }

    fn response(status: &str, body: &str) -> Vec<u8> {
        format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).into_bytes()
    }
    async fn server(
        responses: Vec<Vec<u8>>,
    ) -> (BackendOrigin, tokio::task::JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let origin =
            BackendOrigin::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
        let task = tokio::spawn(async move {
            let mut requests = Vec::new();
            for response in responses {
                let (mut stream, _) =
                    tokio::time::timeout(Duration::from_secs(2), listener.accept())
                        .await
                        .unwrap()
                        .unwrap();
                let mut data = Vec::new();
                while !data.ends_with(b"\r\n\r\n") {
                    let mut byte = [0];
                    stream.read_exact(&mut byte).await.unwrap();
                    data.push(byte[0]);
                    assert!(data.len() < 16384);
                }
                requests.push(String::from_utf8(data).unwrap());
                let _ = stream.write_all(&response).await;
            }
            assert!(
                tokio::time::timeout(Duration::from_millis(75), listener.accept())
                    .await
                    .is_err(),
                "unexpected retry or extra request"
            );
            requests
        });
        (origin, task)
    }

    #[tokio::test]
    async fn listing_authenticates_then_reads_exact_route_without_saving() {
        for version in [0, 1] {
            let config = CONFIG.replace(
                "\"cli_api_version\":0",
                &format!("\"cli_api_version\":{version}"),
            );
            let (origin, server) = server(vec![
                response("200 OK", &config),
                response("200 OK", CATALOG),
            ])
            .await;
            let store = Store::valid(origin.clone());
            let result = run_skills(&origin, &store).await.unwrap();
            assert_eq!(result.skills.len(), 2);
            assert!(result.skills[0].enabled);
            assert!(!result.skills[1].enabled);
            assert_eq!(result.skills[0].version, 1);
            let requests = server.await.unwrap();
            assert!(requests[0].starts_with("GET /api/config HTTP/1.1\r\n"));
            assert!(requests[1].starts_with("GET /api/skills HTTP/1.1\r\n"));
            for request in requests {
                assert!(request
                    .to_lowercase()
                    .contains(&format!("x-api-key: {KEY}\r\n")));
            }
            assert_eq!(*store.events.borrow(), ["available", "read"]);
            assert!(!serde_json::to_string(&result).unwrap().contains(KEY));
            assert!(!format!("{result:?}").contains(KEY));
        }
    }

    #[tokio::test]
    async fn missing_invalid_or_unavailable_key_stops_before_network() {
        let origin = BackendOrigin::parse("http://127.0.0.1:9").unwrap();
        let mut store = Store::valid(origin.clone());
        store.key = Ok(None);
        let error = run_skills(&origin, &store).await.unwrap_err();
        assert_eq!(error, SkillsError::SetupRequired);
        assert_eq!(error.exit_code(), 2);
        store.key = Ok(Some("invalid key".into()));
        assert_eq!(
            run_skills(&origin, &store).await.unwrap_err(),
            SkillsError::Credential(CredentialError::InvalidCredential)
        );
        store.key = Err(CredentialError::AccessDenied);
        assert_eq!(
            run_skills(&origin, &store).await.unwrap_err(),
            SkillsError::Credential(CredentialError::AccessDenied)
        );
        store.events.borrow_mut().clear();
        store.available = Err(CredentialError::UnsupportedPlatform);
        assert_eq!(
            run_skills(&origin, &store).await.unwrap_err(),
            SkillsError::Credential(CredentialError::UnsupportedPlatform)
        );
        assert_eq!(*store.events.borrow(), ["available"]);
    }

    #[tokio::test]
    async fn auth_failure_or_future_version_never_lists_or_retries() {
        for (status, body, expected) in [
            ("401 Unauthorized", KEY, ProbeError::Authentication),
            ("403 Forbidden", KEY, ProbeError::Authentication),
            ("429 Too Many Requests", KEY, ProbeError::LockedOut),
            ("200 OK", "{}", ProbeError::InvalidResponse),
            (
                "200 OK",
                r#"{"cli_api_version":2,"prompt_max_chars":100,"negative_prompt_max_chars":100,"skills_max_selected":5}"#,
                ProbeError::UnsupportedReadVersion,
            ),
        ] {
            let (origin, server) = server(vec![response(status, body)]).await;
            let error = run_skills(&origin, &Store::valid(origin.clone()))
                .await
                .unwrap_err();
            assert_eq!(error, SkillsError::Backend(expected));
            assert!(!format!("{error:?} {error}").contains(KEY));
            assert_eq!(server.await.unwrap().len(), 1);
        }
    }

    #[tokio::test]
    async fn list_failures_do_not_echo_server_text_or_retry() {
        for (status, body, expected) in [
            (
                "401 Unauthorized",
                KEY,
                SkillsError::Backend(ProbeError::Authentication),
            ),
            (
                "403 Forbidden",
                KEY,
                SkillsError::Backend(ProbeError::Authentication),
            ),
            (
                "429 Too Many Requests",
                KEY,
                SkillsError::Backend(ProbeError::LockedOut),
            ),
            ("404 Not Found", KEY, SkillsError::Unavailable),
            (
                "500 Internal Server Error",
                KEY,
                SkillsError::Backend(ProbeError::HttpStatus(500)),
            ),
            (
                "200 OK",
                KEY,
                SkillsError::Backend(ProbeError::InvalidResponse),
            ),
            (
                "200 OK",
                "{}",
                SkillsError::Backend(ProbeError::InvalidResponse),
            ),
        ] {
            let (origin, server) =
                server(vec![response("200 OK", CONFIG), response(status, body)]).await;
            let error = run_skills(&origin, &Store::valid(origin.clone()))
                .await
                .unwrap_err();
            assert_eq!(error, expected);
            assert!(!format!("{error:?} {error}").contains(KEY));
            assert_eq!(server.await.unwrap().len(), 2);
        }
    }

    #[tokio::test]
    async fn list_redirect_is_not_followed() {
        let destination = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let redirect = format!("HTTP/1.1 302 Found\r\nLocation: http://{}/secret\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", destination.local_addr().unwrap());
        let (origin, server) =
            server(vec![response("200 OK", CONFIG), redirect.into_bytes()]).await;
        assert_eq!(
            run_skills(&origin, &Store::valid(origin.clone()))
                .await
                .unwrap_err(),
            SkillsError::Backend(ProbeError::Redirect)
        );
        server.await.unwrap();
        assert!(
            tokio::time::timeout(Duration::from_millis(75), destination.accept())
                .await
                .is_err()
        );
    }

    #[tokio::test]
    async fn list_body_is_bounded_with_and_without_content_length() {
        for bytes in [
            format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                crate::http::MAX_CONFIG_BYTES + 1
            )
            .into_bytes(),
            format!(
                "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n{}",
                "x".repeat(crate::http::MAX_CONFIG_BYTES + 1)
            )
            .into_bytes(),
        ] {
            let (origin, server) = server(vec![response("200 OK", CONFIG), bytes]).await;
            assert_eq!(
                run_skills(&origin, &Store::valid(origin.clone()))
                    .await
                    .unwrap_err(),
                SkillsError::Backend(ProbeError::ResponseTooLarge)
            );
            server.await.unwrap();
        }
    }

    #[test]
    fn summaries_reject_lossy_types_missing_fields_and_duplicate_ids() {
        let base: serde_json::Value = serde_json::from_str(CATALOG).unwrap();
        for (key, value) in [
            ("enabled", json!("true")),
            ("version", json!("1")),
            ("version", json!(-1)),
            ("id", json!(" ")),
            ("name", json!("")),
            ("source", json!(null)),
        ] {
            let mut bad = base.clone();
            bad["skills"][0][key] = value;
            assert!(
                SkillCatalog::decode(&serde_json::to_vec(&bad).unwrap()).is_err(),
                "{key}"
            );
        }
        for field in ["id", "name", "description", "source", "version", "enabled"] {
            let mut bad = base.clone();
            bad["skills"][0].as_object_mut().unwrap().remove(field);
            assert!(SkillCatalog::decode(&serde_json::to_vec(&bad).unwrap()).is_err());
        }
        let mut bad = base;
        bad["skills"][1]["id"] = json!("first");
        assert!(SkillCatalog::decode(&serde_json::to_vec(&bad).unwrap()).is_err());
    }

    #[test]
    fn empty_and_untrusted_summaries_are_honest_and_terminal_safe() {
        assert!(SkillCatalog::decode(br#"{"skills":[]}"#)
            .unwrap()
            .terminal_summary()
            .contains("No installed skills"));
        let mut catalog = SkillCatalog::decode(CATALOG.as_bytes()).unwrap();
        catalog.skills[0].description = "Escape\u{1b}[2J\r\u{202e}text".into();
        let display = catalog.terminal_summary();
        assert!(display.contains("disabled"));
        assert!(display.contains("v1"));
        assert!(display.contains("not connected"));
        assert!(
            !display.contains('\u{1b}') && !display.contains('\r') && !display.contains('\u{202e}')
        );
        let value = serde_json::to_value(catalog).unwrap();
        assert_eq!(
            value["skills"][0]["description"],
            "Escape\u{1b}[2J\r\u{202e}text"
        );
    }
}
