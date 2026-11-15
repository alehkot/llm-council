#![cfg(unix)]
use clap::Parser;
use council_cli::{args::Cli, commands, config::BackendOrigin, http::ConfigClient};
use std::{
    io::{BufRead, BufReader},
    process::{Child, Command, Stdio},
};
struct Backend(Child);
impl Drop for Backend {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[tokio::test]
async fn real_backend_commands_complete_and_reopen_without_keychain_or_providers() {
    let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap();
    let home = repo.join(format!(
        "cli/target/integration-home-{}",
        std::process::id()
    ));
    std::fs::create_dir_all(&home).unwrap();
    std::env::set_var("HOME", &home);
    let child = Command::new("uv")
        .args(["run", "python", "tests/fixtures/serve_basic_runs.py"])
        .current_dir(repo)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap();
    let mut backend = Backend(child);
    let mut port = String::new();
    BufReader::new(backend.0.stdout.take().unwrap())
        .read_line(&mut port)
        .unwrap();
    let origin = BackendOrigin::parse(&format!("http://127.0.0.1:{}", port.trim())).unwrap();
    let mut client = ConfigClient::new(origin.clone(), "synthetic-cli-integration").unwrap();
    for attempt in 0..50 {
        if client.basic_config().await.is_ok() {
            break;
        }
        assert!(attempt < 49);
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
    let ask = Cli::try_parse_from([
        "council",
        "--non-interactive",
        "ask",
        "Question",
        "--detach",
    ])
    .unwrap();
    assert_eq!(
        commands::execute(&ask, &origin, &mut client).await.unwrap(),
        0
    );
    let receipt = std::fs::read_dir(home.join(".council-receipts"))
        .unwrap()
        .filter_map(Result::ok)
        .map(|f| f.path())
        .find(|p| p.extension().is_some_and(|e| e == "json"))
        .unwrap();
    let run = uuid::Uuid::parse_str(receipt.file_stem().unwrap().to_str().unwrap()).unwrap();
    let raw = std::fs::read_to_string(&receipt).unwrap();
    assert!(!raw.contains("synthetic-cli-integration"));
    let id = run.to_string();
    let paused = Cli::try_parse_from(["council", "--non-interactive", "resume", &id]).unwrap();
    assert_eq!(
        commands::execute(&paused, &origin, &mut client)
            .await
            .unwrap(),
        3
    );
    let http = reqwest::Client::new();
    for _ in 0..2 {
        let mut state = commands::read(&mut client, run, None).await.unwrap();
        for _ in 0..100 {
            if state.pending_prompt.is_some() {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
            state = commands::read(&mut client, run, None).await.unwrap();
        }
        let instance = state.pending_prompt.unwrap().instance_id;
        let response = http
            .post(format!(
                "{}api/basic-runs/{run}/answers/{instance}",
                origin.as_url()
            ))
            .header("X-API-Key", "synthetic-cli-integration")
            .header("Content-Type", "application/json")
            .body(r#"{"skipped":true,"responses":{}}"#)
            .send()
            .await
            .unwrap();
        assert!(response.status().is_success());
    }
    let id = run.to_string();
    for args in [
        vec!["council", "--non-interactive", "resume", &id],
        vec!["council", "show", &id],
        vec!["council", "show", &id, "--stage", "reviews"],
        vec!["council", "history"],
    ] {
        assert_eq!(
            commands::execute(&Cli::try_parse_from(args).unwrap(), &origin, &mut client)
                .await
                .unwrap(),
            0
        );
    }
    let output = home.join("answer.md");
    let export = Cli::try_parse_from([
        "council",
        "export",
        &id,
        "--output",
        output.to_str().unwrap(),
    ])
    .unwrap();
    assert_eq!(
        commands::execute(&export, &origin, &mut client)
            .await
            .unwrap(),
        0
    );
    assert!(std::fs::read_to_string(&output)
        .unwrap()
        .contains("Final answer"));
    assert!(commands::execute(&export, &origin, &mut client)
        .await
        .is_err());
    let mut bad_key = ConfigClient::new(origin.clone(), "wrong-key").unwrap();
    assert!(bad_key.basic_config().await.is_err());
    // A crash after local persistence but before HTTP submission recovers the same ID.
    let pending_id = uuid::Uuid::parse_str("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").unwrap();
    let request = serde_json::from_value(serde_json::json!({
        "content": "Recovered question", "negative_prompt": null, "skill_ids": [],
        "refine": false, "interaction_policy": "persistent", "recovery_policy": "explicit_retry"
    }))
    .unwrap();
    let store = council_cli::receipt_store::ReceiptStore::user_store().unwrap();
    store.prepare(pending_id, &origin, &request).unwrap();
    let resume = Cli::try_parse_from([
        "council",
        "--non-interactive",
        "resume",
        &pending_id.to_string(),
    ])
    .unwrap();
    assert_eq!(
        commands::execute(&resume, &origin, &mut client)
            .await
            .unwrap(),
        3
    );
    assert_eq!(
        commands::read(&mut client, pending_id, None)
            .await
            .unwrap()
            .run_id,
        pending_id
    );
    assert!(matches!(
        store.load(pending_id, &origin).unwrap(),
        council_cli::receipt_store::SubmissionState::Acknowledged { .. }
    ));
    std::fs::remove_dir_all(home).unwrap();
}
