use serde::Deserialize;
use url::{Host, Url};

pub const REQUIRED_API_VERSION: u32 = 1;
const INVALID_ORIGIN: &str =
    "Backend URL must be an HTTP(S) loopback origin without credentials, path, query, or fragment.";

/// Validated local origin used to bind the configuration transport.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BackendOrigin(Url);

impl BackendOrigin {
    pub fn parse(raw: &str) -> Result<Self, &'static str> {
        if raw.chars().any(char::is_whitespace) || raw.chars().any(char::is_control) {
            return Err(INVALID_ORIGIN);
        }
        let mut url = Url::parse(raw).map_err(|_| INVALID_ORIGIN)?;
        if !matches!(url.scheme(), "http" | "https")
            || !url.username().is_empty()
            || url.password().is_some()
            || url.path() != "/"
            || url.query().is_some()
            || url.fragment().is_some()
            || url.port() == Some(0)
        {
            return Err(INVALID_ORIGIN);
        }
        match url.host() {
            Some(Host::Ipv4(ip)) if ip.is_loopback() => {}
            Some(Host::Ipv6(ip)) if ip.is_loopback() => {}
            // Pin localhost to loopback rather than trusting DNS/hosts resolution.
            Some(Host::Domain("localhost")) => {
                url.set_host(Some("127.0.0.1"))
                    .map_err(|_| INVALID_ORIGIN)?;
            }
            _ => return Err(INVALID_ORIGIN),
        }
        Ok(Self(url))
    }

    pub fn as_url(&self) -> &Url {
        &self.0
    }
}

/// Only the fields used by the client; unrelated /api/config fields are allowed.
/// Missing version means an old backend, never implicit v1 support.
#[derive(Debug, Deserialize)]
pub struct BackendConfig {
    #[serde(default)]
    pub cli_api_version: u32,
    #[serde(default)]
    pub council_models: Vec<String>,
    #[serde(default)]
    pub chairman_model: String,
    pub prompt_max_chars: usize,
    pub negative_prompt_max_chars: usize,
    pub skills_max_selected: usize,
}

impl BackendConfig {
    pub fn require_supported(&self) -> Result<(), &'static str> {
        if self.cli_api_version != REQUIRED_API_VERSION {
            return Err("This client requires CLI API v1. Update the backend; legacy submission and automatic retry are not safe fallbacks.");
        }
        self.require_valid_limits()
    }

    pub fn require_valid_limits(&self) -> Result<(), &'static str> {
        if self.prompt_max_chars == 0
            || self.negative_prompt_max_chars == 0
            || self.skills_max_selected == 0
        {
            return Err("Backend reported invalid input limits.");
        }
        Ok(())
    }
}
