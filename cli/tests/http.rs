use council_cli::{
    config::BackendOrigin,
    http::{ConfigClient, ProbeError, MAX_CONFIG_BYTES},
};
use std::{process::Command, time::Duration};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    task::JoinHandle,
};

const KEY: &str = "synthetic-secret-DO-NOT-ECHO";
const CONFIG: &str = r#"{"cli_api_version":1,"prompt_max_chars":100,"negative_prompt_max_chars":50,"skills_max_selected":3,"unrelated":true}"#;

async fn read_headers(stream: &mut TcpStream) -> String {
    let mut data = Vec::new();
    while !data.ends_with(b"\r\n\r\n") {
        let mut byte = [0];
        stream.read_exact(&mut byte).await.unwrap();
        data.push(byte[0]);
        assert!(data.len() < 16_384);
    }
    String::from_utf8(data).unwrap()
}

async fn server(response: Vec<u8>) -> (BackendOrigin, JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin =
        BackendOrigin::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let request = read_headers(&mut stream).await;
        // A size/header failure can intentionally close before the entire body.
        let _ = stream.write_all(&response).await;
        request
    });
    (origin, task)
}

fn response(status: &str, body: &str) -> Vec<u8> {
    format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).into_bytes()
}

#[tokio::test]
async fn probes_exact_origin_path_with_in_memory_key() {
    let (origin, task) = server(response("200 OK", CONFIG)).await;
    let mut client = ConfigClient::new(origin, KEY).unwrap();
    assert!(!format!("{client:?}").contains(KEY));
    let config = client.probe().await.unwrap();
    assert_eq!(config.prompt_max_chars, 100);
    let request = task.await.unwrap();
    assert!(request.starts_with("GET /api/config HTTP/1.1\r\n"));
    assert!(request.contains(&format!("x-api-key: {KEY}\r\n")));
    assert!(request.contains("accept: application/json\r\n"));
}

#[tokio::test]
async fn reuses_one_http_connection_for_safe_config_reads() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin =
        BackendOrigin::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        for _ in 0..2 {
            read_headers(&mut stream).await;
            let body = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{CONFIG}",
                CONFIG.len()
            );
            stream.write_all(body.as_bytes()).await.unwrap();
        }
    });
    let mut client = ConfigClient::new(origin, KEY).unwrap();
    client.probe().await.unwrap();
    client.probe().await.unwrap();
    tokio::time::timeout(Duration::from_secs(2), task)
        .await
        .unwrap()
        .unwrap();
}

#[tokio::test]
async fn redirects_never_forward_key_or_request_to_another_origin() {
    let destination = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let redirect = format!("HTTP/1.1 302 Found\r\nLocation: http://{}/stolen\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", destination.local_addr().unwrap());
    let (origin, task) = server(redirect.into_bytes()).await;
    let mut client = ConfigClient::new(origin, KEY).unwrap();
    assert_eq!(client.probe().await.unwrap_err(), ProbeError::Redirect);
    task.await.unwrap();
    assert!(
        tokio::time::timeout(Duration::from_millis(100), destination.accept())
            .await
            .is_err()
    );
}

#[tokio::test]
async fn rejected_authentication_and_lockout_stop_future_requests() {
    for (status, expected) in [
        ("401 Unauthorized", ProbeError::Authentication),
        ("403 Forbidden", ProbeError::Authentication),
        ("429 Too Many Requests", ProbeError::LockedOut),
    ] {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let origin =
            BackendOrigin::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
        let task = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let headers = read_headers(&mut stream).await;
            stream.write_all(&response(status, KEY)).await.unwrap();
            drop(stream);
            assert!(
                tokio::time::timeout(Duration::from_millis(150), listener.accept())
                    .await
                    .is_err()
            );
            headers
        });
        let mut client = ConfigClient::new(origin, KEY).unwrap();
        let error = client.probe().await.unwrap_err();
        assert_eq!(error, expected);
        assert!(!format!("{error} {error:?} {client:?}").contains(KEY));
        assert_eq!(
            client.probe().await.unwrap_err(),
            ProbeError::AuthenticationStopped
        );
        task.await.unwrap();
    }
}

#[tokio::test]
async fn errors_do_not_echo_server_text_keys_or_parser_details() {
    for (status, body, expected) in [
        ("500 Broken", KEY.to_string(), ProbeError::HttpStatus(500)),
        (
            "200 OK",
            format!("{{\"cli_api_version\":\"{KEY}\"}}"),
            ProbeError::InvalidResponse,
        ),
        (
            "200 OK",
            CONFIG.replace(":1,", ":0,"),
            ProbeError::UnsupportedVersion,
        ),
        (
            "200 OK",
            CONFIG.replace(":1,", ":2,"),
            ProbeError::UnsupportedVersion,
        ),
        (
            "200 OK",
            CONFIG.replace("\"cli_api_version\":1,", ""),
            ProbeError::UnsupportedVersion,
        ),
        (
            "200 OK",
            CONFIG.replace(":100,", ":0,"),
            ProbeError::InvalidLimits,
        ),
        (
            "200 OK",
            "not JSON".to_string(),
            ProbeError::InvalidResponse,
        ),
    ] {
        let (origin, task) = server(response(status, &body)).await;
        let mut client = ConfigClient::new(origin, KEY).unwrap();
        let error = client.probe().await.unwrap_err();
        assert_eq!(error, expected);
        assert!(!format!("{error} {error:?}").contains(KEY));
        task.await.unwrap();
    }
}

#[tokio::test]
async fn bounds_declared_and_chunked_response_bodies() {
    for wire in [
        format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n",
            MAX_CONFIG_BYTES + 1
        )
        .into_bytes(),
        format!(
            "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n{:x}\r\n{}\r\n0\r\n\r\n",
            MAX_CONFIG_BYTES + 1,
            "x".repeat(MAX_CONFIG_BYTES + 1)
        )
        .into_bytes(),
    ] {
        let (origin, task) = server(wire).await;
        let mut client = ConfigClient::new(origin, KEY).unwrap();
        assert_eq!(
            client.probe().await.unwrap_err(),
            ProbeError::ResponseTooLarge
        );
        task.await.unwrap();
    }
}

#[tokio::test]
async fn accepts_chunked_config_and_rejects_truncated_or_invalid_utf8() {
    let chunked = format!(
        "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n{:x}\r\n{CONFIG}\r\n0\r\n\r\n",
        CONFIG.len()
    );
    let (origin, task) = server(chunked.into_bytes()).await;
    ConfigClient::new(origin, KEY)
        .unwrap()
        .probe()
        .await
        .unwrap();
    task.await.unwrap();
    for wire in [
        b"HTTP/1.1 200 OK\r\nContent-Length: 99\r\n\r\n{}".to_vec(),
        b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\n\xff".to_vec(),
    ] {
        let (origin, task) = server(wire).await;
        let mut client = ConfigClient::new(origin, KEY).unwrap();
        assert_eq!(
            client.probe().await.unwrap_err(),
            ProbeError::InvalidResponse
        );
        task.await.unwrap();
    }
}

#[tokio::test]
async fn rejects_invalid_header_keys_before_request() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin =
        BackendOrigin::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
    for key in ["", " ", "a\rb", "a\nb", "a\tb", "a\0b", "é"] {
        assert_eq!(
            ConfigClient::new(origin.clone(), key).unwrap_err(),
            ProbeError::InvalidCredential
        );
    }
    assert!(
        tokio::time::timeout(Duration::from_millis(100), listener.accept())
            .await
            .is_err()
    );
}

#[tokio::test]
async fn unavailable_backend_has_start_command_without_retry() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin =
        BackendOrigin::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
    drop(listener);
    let error = ConfigClient::new(origin, KEY)
        .unwrap()
        .probe()
        .await
        .unwrap_err();
    assert_eq!(error, ProbeError::Connection);
    assert!(error.to_string().contains("uv run python -m backend.main"));
}

#[test]
fn proxy_environment_does_not_change_transport_destination() {
    // Run in a separate process to avoid mutating other parallel tests' environment.
    let status = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "proxy_child", "--nocapture"])
        .env("COUNCIL_PROXY_TEST_CHILD", "1")
        .env("HTTP_PROXY", "http://127.0.0.1:1")
        .env("http_proxy", "http://127.0.0.1:1")
        .env("HTTPS_PROXY", "http://127.0.0.1:1")
        .env("ALL_PROXY", "http://127.0.0.1:1")
        .env("all_proxy", "http://127.0.0.1:1")
        .env("NO_PROXY", "")
        .env("no_proxy", "")
        .status()
        .unwrap();
    assert!(status.success());
}

#[tokio::test]
async fn proxy_child() {
    if std::env::var_os("COUNCIL_PROXY_TEST_CHILD").is_none() {
        return;
    }
    let (origin, task) = server(response("200 OK", CONFIG)).await;
    // The public origin validator pins localhost so this does not use DNS.
    let origin =
        BackendOrigin::parse(&origin.as_url().as_str().replace("127.0.0.1", "localhost")).unwrap();
    assert_eq!(origin.as_url().host_str(), Some("127.0.0.1"));
    ConfigClient::new(origin, KEY)
        .unwrap()
        .probe()
        .await
        .unwrap();
    task.await.unwrap();
}

#[tokio::test]
async fn dropped_connection_is_not_retried() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin =
        BackendOrigin::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        read_headers(&mut stream).await;
        // The authenticated request arrived, but its response was lost.
        drop(stream);
        assert!(
            tokio::time::timeout(Duration::from_millis(200), listener.accept())
                .await
                .is_err()
        );
    });
    let mut client = ConfigClient::new(origin, KEY).unwrap();
    assert_eq!(
        client.probe().await.unwrap_err(),
        ProbeError::InvalidResponse
    );
    task.await.unwrap();
}
