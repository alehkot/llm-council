//! Origin-bound configuration probe. No consultation mutation or credential store.
use std::{fmt, time::Duration};

use reqwest::{header::HeaderValue, redirect::Policy, Client, StatusCode};

use crate::config::{BackendConfig, BackendOrigin, REQUIRED_API_VERSION};

pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(35);
pub const MAX_CONFIG_BYTES: usize = 1024 * 1024;

/// Intentionally contains no server text, credential, URL or underlying error.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProbeError {
    InvalidCredential,
    ClientInitialization,
    Authentication,
    LockedOut,
    AuthenticationStopped,
    Connection,
    Timeout,
    Redirect,
    HttpStatus(u16),
    ResponseTooLarge,
    InvalidResponse,
    UnsupportedVersion,
    InvalidLimits,
}

impl fmt::Display for ProbeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidCredential => f.write_str("The backend key is empty or contains invalid header characters."),
            Self::ClientInitialization => f.write_str("Could not initialize the local HTTP client."),
            Self::Authentication => f.write_str("Backend authentication failed. Check the key before reconnecting; no retry was made."),
            Self::LockedOut => f.write_str("Backend requests are rate limited or locked out. Stop and check the backend before reconnecting."),
            Self::AuthenticationStopped => f.write_str("Authentication probing has stopped after a rejected key or lockout. Reconfigure before reconnecting."),
            Self::Connection => f.write_str("Could not reach the local backend. From the project root run: uv run python -m backend.main"),
            Self::Timeout => f.write_str("The local backend request timed out. No retry was made."),
            Self::Redirect => f.write_str("The local backend returned a redirect, which was not followed."),
            Self::HttpStatus(status) => write!(f, "The local backend returned HTTP {status}. No retry was made."),
            Self::ResponseTooLarge => f.write_str("The backend configuration response exceeded its size limit."),
            Self::InvalidResponse => f.write_str("The backend did not return a complete valid configuration response."),
            Self::UnsupportedVersion => f.write_str("This client requires CLI API v1. Update the backend; no legacy submission fallback is available."),
            Self::InvalidLimits => f.write_str("The backend reported invalid input limits."),
        }
    }
}
impl std::error::Error for ProbeError {}

/// One validated origin and in-memory key per client. A fresh client is required
/// after authentication rejection. Its only request is GET /api/config.
/// Debug output deliberately excludes the client/header containing the key.
pub struct ConfigClient {
    client: Client,
    origin: BackendOrigin,
    key: HeaderValue,
    authentication_stopped: bool,
}

impl fmt::Debug for ConfigClient {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ConfigClient")
            .field("origin", &self.origin)
            .field("key", &"[REDACTED]")
            .field("authentication_stopped", &self.authentication_stopped)
            .finish()
    }
}

impl ConfigClient {
    pub fn new(origin: BackendOrigin, key: &str) -> Result<Self, ProbeError> {
        Self::with_timeout(origin, key, REQUEST_TIMEOUT)
    }

    fn with_timeout(
        origin: BackendOrigin,
        key: &str,
        timeout: Duration,
    ) -> Result<Self, ProbeError> {
        if key.is_empty() || key.bytes().any(|byte| !(0x21..=0x7e).contains(&byte)) {
            return Err(ProbeError::InvalidCredential);
        }
        let mut key = HeaderValue::from_str(key).map_err(|_| ProbeError::InvalidCredential)?;
        key.set_sensitive(true);
        let client = Client::builder()
            .no_proxy()
            .redirect(Policy::none())
            .retry(reqwest::retry::never())
            .timeout(timeout)
            .connect_timeout(Duration::from_secs(5).min(timeout))
            .build()
            .map_err(|_| ProbeError::ClientInitialization)?;
        Ok(Self {
            client,
            origin,
            key,
            authentication_stopped: false,
        })
    }

    /// Authenticate once per call; never retry internally. API0 is reported as
    /// unsupported, never treated as permission to use older mutation routes.
    pub async fn probe(&mut self) -> Result<BackendConfig, ProbeError> {
        if self.authentication_stopped {
            return Err(ProbeError::AuthenticationStopped);
        }
        let mut url = self.origin.as_url().clone();
        url.set_path("/api/config");
        let mut response = self
            .client
            .get(url)
            .header("X-API-Key", self.key.clone())
            .header("Accept", "application/json")
            .send()
            .await
            .map_err(classify_transport)?;
        let status = response.status();
        if status == StatusCode::UNAUTHORIZED || status == StatusCode::FORBIDDEN {
            self.authentication_stopped = true;
            return Err(ProbeError::Authentication);
        }
        if status == StatusCode::TOO_MANY_REQUESTS {
            self.authentication_stopped = true;
            return Err(ProbeError::LockedOut);
        }
        if status.is_redirection() {
            return Err(ProbeError::Redirect);
        }
        if !status.is_success() {
            return Err(ProbeError::HttpStatus(status.as_u16()));
        }
        if response
            .content_length()
            .is_some_and(|size| size > MAX_CONFIG_BYTES as u64)
        {
            return Err(ProbeError::ResponseTooLarge);
        }
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(classify_transport)? {
            if chunk.len() > MAX_CONFIG_BYTES - body.len() {
                return Err(ProbeError::ResponseTooLarge);
            }
            body.extend_from_slice(&chunk);
        }
        let config: BackendConfig =
            serde_json::from_slice(&body).map_err(|_| ProbeError::InvalidResponse)?;
        if config.cli_api_version != REQUIRED_API_VERSION {
            return Err(ProbeError::UnsupportedVersion);
        }
        config
            .require_supported()
            .map_err(|_| ProbeError::InvalidLimits)?;
        Ok(config)
    }
}

fn classify_transport(error: reqwest::Error) -> ProbeError {
    if error.is_timeout() {
        ProbeError::Timeout
    } else if error.is_connect() {
        ProbeError::Connection
    } else {
        ProbeError::InvalidResponse
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn bounds_stalled_headers_and_body() {
        use tokio::{
            io::{AsyncReadExt, AsyncWriteExt},
            net::TcpListener,
        };
        for partial in ["", "HTTP/1.1 200 OK\r\nContent-Length: 200\r\n\r\n{"] {
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            let origin =
                BackendOrigin::parse(&format!("http://{}", listener.local_addr().unwrap()))
                    .unwrap();
            let server = tokio::spawn(async move {
                let (mut stream, _) = listener.accept().await.unwrap();
                let mut request = Vec::new();
                while !request.ends_with(b"\r\n\r\n") {
                    let mut byte = [0];
                    stream.read_exact(&mut byte).await.unwrap();
                    request.push(byte[0]);
                }
                stream.write_all(partial.as_bytes()).await.unwrap();
                tokio::time::sleep(Duration::from_secs(5)).await;
            });
            let mut client =
                ConfigClient::with_timeout(origin, "synthetic-key", Duration::from_millis(100))
                    .unwrap();
            assert_eq!(client.probe().await.unwrap_err(), ProbeError::Timeout);
            server.abort();
            let _ = server.await;
        }
        assert!(REQUEST_TIMEOUT > Duration::from_secs(25));
    }
}
