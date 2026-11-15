//! Basic-run command integration. Backend execution is independent of this process.
use crate::{
    args::{Cli, Command, Stage},
    config::BackendOrigin,
    http::{ConfigClient, ProbeError},
    output::terminal_text,
    prompt::{Collected, Limits},
    receipt_store::{ReceiptStore, StoreError, SubmissionState},
    wire::{RunLifecycle, RunReceipt, RunSnapshot},
};
use reqwest::Method;
use serde_json::{json, Value};
use std::{
    fs::OpenOptions,
    io::{Read, Write},
    os::unix::fs::OpenOptionsExt,
    sync::atomic::{AtomicBool, Ordering},
};
use uuid::Uuid;

static INTERRUPTED: AtomicBool = AtomicBool::new(false);
extern "C" fn interrupt(_: libc::c_int) {
    INTERRUPTED.store(true, Ordering::Relaxed);
}
struct SignalGuard(libc::sigaction);
impl SignalGuard {
    fn install() -> Result<Self, String> {
        INTERRUPTED.store(false, Ordering::Relaxed);
        // SAFETY: both initialized sigaction structs are valid for this call.
        unsafe {
            let mut action: libc::sigaction = std::mem::zeroed();
            let mut old: libc::sigaction = std::mem::zeroed();
            action.sa_sigaction = interrupt as *const () as usize;
            libc::sigemptyset(&mut action.sa_mask);
            if libc::sigaction(libc::SIGINT, &action, &mut old) != 0 {
                return Err("Could not install detach handler".into());
            }
            Ok(Self(old))
        }
    }
}
impl Drop for SignalGuard {
    fn drop(&mut self) {
        unsafe {
            libc::sigaction(libc::SIGINT, &self.0, std::ptr::null_mut());
        }
    }
}
fn error(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn store_error(_: StoreError) -> String {
    "Local submission receipt unavailable or unsafe; no new submission was sent.".into()
}
fn reconnect(origin: &BackendOrigin, run: Uuid) {
    eprintln!(
        "Reconnect: council --backend-url '{}' resume {run}",
        origin.as_url()
    );
}
fn decode(bytes: &[u8], run: Uuid) -> Result<RunSnapshot, String> {
    let state: RunSnapshot = serde_json::from_slice(bytes).map_err(|_| "Invalid run snapshot")?;
    if state.run_id != run
        || state.conversation_id.as_str() != format!("cli-{run}")
        || state.assistant_index != Some(1)
    {
        return Err("Backend returned a different run identity".into());
    }
    Ok(state)
}
pub async fn read(
    client: &mut ConfigClient,
    run: Uuid,
    cursor: Option<&str>,
) -> Result<RunSnapshot, String> {
    let query = cursor
        .map(|c| vec![("cursor", c), ("wait", "1")])
        .unwrap_or_default();
    let bytes = client
        .basic_request(Method::GET, Some(run), "", &query, None)
        .await
        .map_err(error)?;
    decode(&bytes, run)
}
async fn mutate(
    client: &mut ConfigClient,
    run: Uuid,
    suffix: &str,
    body: Option<Value>,
) -> Result<RunSnapshot, String> {
    let bytes = client
        .basic_request(Method::POST, Some(run), suffix, &[], body)
        .await
        .map_err(error)?;
    decode(&bytes, run)
}
async fn submit(
    client: &mut ConfigClient,
    origin: &BackendOrigin,
    store: &ReceiptStore,
    run: Uuid,
    payload: Value,
) -> Result<RunSnapshot, String> {
    let bytes = client
        .basic_request(Method::PUT, Some(run), "", &[], Some(payload))
        .await
        .map_err(error)?;
    let state = decode(&bytes, run)?;
    store
        .acknowledge(
            run,
            origin,
            &RunReceipt {
                run_id: run,
                conversation_id: state.conversation_id.clone(),
                assistant_index: state.assistant_index,
                lifecycle: state.lifecycle.clone(),
            },
        )
        .map_err(|_| "The backend accepted this run, but its local acknowledgment could not be saved. Use the printed resume command; do not repeat ask.".to_owned())?;
    Ok(state)
}
fn display(state: &RunSnapshot, json_output: bool, stage: Option<&Stage>) -> Result<(), String> {
    if json_output {
        println!("{}", json!(state));
        return Ok(());
    }
    if let Some(stage) = stage {
        let key = match stage {
            Stage::Answers => "stage1",
            Stage::Reviews => "stage2",
            Stage::Negative => "negative",
            Stage::Deliberation => "deliberation",
            Stage::All => "",
        };
        let value = if key.is_empty() {
            json!(state.stages)
        } else {
            state.stages.get(key).cloned().unwrap_or(Value::Null)
        };
        println!(
            "{}",
            terminal_text(&serde_json::to_string_pretty(&value).map_err(error)?)
        );
    } else if let Some(stage3) = state.stages.get("stage3") {
        let current = stage3.get("current").unwrap_or(stage3);
        let content = current
            .get("response")
            .and_then(Value::as_str)
            .ok_or("Final answer is missing")?;
        println!("{}", terminal_text(content));
    } else {
        println!(
            "{}: {:?} ({})",
            state.run_id,
            state.lifecycle,
            terminal_text(state.stage.as_deref().unwrap_or("pending"))
        );
    }
    Ok(())
}
async fn follow(
    cli: &Cli,
    origin: &BackendOrigin,
    client: &mut ConfigClient,
    mut state: RunSnapshot,
) -> Result<u8, String> {
    let mut last = None;
    loop {
        if INTERRUPTED.load(Ordering::Relaxed) {
            display(&state, cli.json, None)?;
            return Ok(130);
        }
        if last.as_ref() != state.stage.as_ref() {
            eprintln!(
                "{}: {}",
                state.run_id,
                terminal_text(state.stage.as_deref().unwrap_or("queued"))
            );
            last = state.stage.clone();
        }
        match state.lifecycle {
            RunLifecycle::Complete => {
                display(&state, cli.json, None)?;
                return Ok(0);
            }
            RunLifecycle::Failed | RunLifecycle::Cancelled => {
                display(&state, cli.json, None)?;
                if let Some(message) = &state.error {
                    eprintln!("{}", terminal_text(message));
                }
                return Ok(1);
            }
            RunLifecycle::NeedsRetry => {
                let notice = state.retry.as_ref().ok_or("Missing retry notice")?;
                eprintln!("{}", terminal_text(&notice.explanation));
                eprintln!(
                    "Explicit consent: council --backend-url '{}' retry {} --revision {}",
                    origin.as_url(),
                    state.run_id,
                    terminal_text(&notice.revision)
                );
                display(&state, cli.json, None)?;
                return Ok(3);
            }
            RunLifecycle::WaitingInput => {
                let prompt = state.pending_prompt.as_ref().ok_or("Missing prompt")?;
                let collected = crate::prompt_terminal::collect_from_terminal(
                    state.run_id,
                    prompt,
                    !cli.non_interactive,
                    Limits {
                        fields: 100,
                        options_per_field: 1000,
                        text_chars: 100_000,
                    },
                );
                match collected {
                    Ok(Collected::Answer {
                        instance_id,
                        answer,
                        ..
                    }) => {
                        state = mutate(
                            client,
                            state.run_id,
                            &format!("/answers/{instance_id}"),
                            Some(json!(answer)),
                        )
                        .await?;
                        continue;
                    }
                    _ => {
                        display(&state, cli.json, None)?;
                        return Ok(if INTERRUPTED.load(Ordering::Relaxed) {
                            130
                        } else {
                            3
                        });
                    }
                }
            }
            _ => {}
        }
        state = read(client, state.run_id, Some(&state.cursor)).await?;
    }
}

/// Injectable client keeps tests off the user's Keychain and real backend.
pub async fn execute(
    cli: &Cli,
    origin: &BackendOrigin,
    client: &mut ConfigClient,
) -> Result<u8, String> {
    let _signals = SignalGuard::install()?;
    let config = client.basic_config().await.map_err(error)?;
    match &cli.command {
        Command::Ask(ask) => {
            if ask.refine {
                return Err("Basic runs do not yet support --refine".into());
            }
            if !config.cli_basic_requirements.is_empty() {
                return Err(format!(
                    "Basic runs require backend settings: {}",
                    terminal_text(&config.cli_basic_requirements.join(", "))
                ));
            }
            let request =
                crate::ask_input::prepare(ask, &config, cli.non_interactive).map_err(|e| {
                    format!(
                        "Question input unavailable: {e:?}. Supply text, --file, or '-' for stdin."
                    )
                })?;
            let mut bytes = [0u8; 16];
            std::fs::File::open("/dev/urandom")
                .and_then(|mut f| f.read_exact(&mut bytes))
                .map_err(|_| "Could not generate run identity")?;
            let run = uuid::Builder::from_random_bytes(bytes).into_uuid();
            let store = ReceiptStore::user_store().map_err(store_error)?;
            store.prepare(run, origin, &request).map_err(store_error)?;
            reconnect(origin, run);
            let state = submit(client, origin, &store, run, json!(request)).await?;
            if ask.detach {
                display(&state, cli.json, None)?;
                Ok(0)
            } else {
                follow(cli, origin, client, state).await
            }
        }
        Command::Resume { run } => {
            reconnect(origin, *run);
            // Only replay the exact durably saved request, with the original ID/origin.
            match client
                .basic_request(Method::GET, Some(*run), "", &[], None)
                .await
            {
                Ok(bytes) => {
                    decode(&bytes, *run)?;
                }
                Err(ProbeError::HttpStatus(404 | 409)) => {
                    let store = ReceiptStore::user_store().map_err(store_error)?;
                    let pending = store.load(*run, origin).map_err(store_error)?;
                    let SubmissionState::Pending { payload } = pending else {
                        return Err("Acknowledged run is unavailable; no resubmission made".into());
                    };
                    submit(
                        client,
                        origin,
                        &store,
                        *run,
                        serde_json::from_str(&payload).map_err(error)?,
                    )
                    .await?;
                }
                Err(e) => return Err(error(e)),
            }
            let state = mutate(client, *run, "/resume", None).await?;
            follow(cli, origin, client, state).await
        }
        Command::Retry { run, revision } => {
            reconnect(origin, *run);
            let state = mutate(client, *run, "/retry", Some(json!({"revision":revision}))).await?;
            follow(cli, origin, client, state).await
        }
        Command::Show { run, stage } => {
            let state = read(client, *run, None).await?;
            display(&state, cli.json, stage.as_ref())?;
            Ok(0)
        }
        Command::Cancel { run } => {
            let current = read(client, *run, None).await?;
            let state = mutate(
                client,
                *run,
                "/cancel",
                Some(json!({"revision":current.cursor})),
            )
            .await?;
            display(&state, cli.json, None)?;
            Ok(0)
        }
        Command::History => {
            let bytes = client
                .basic_request(Method::GET, None, "", &[], None)
                .await
                .map_err(error)?;
            let states: Vec<RunSnapshot> =
                serde_json::from_slice(&bytes).map_err(|_| "Invalid history")?;
            if cli.json {
                println!("{}", json!(states));
            } else {
                for state in states {
                    println!(
                        "{} {:?} {}",
                        state.run_id,
                        state.lifecycle,
                        terminal_text(
                            state
                                .provenance
                                .get("created_at")
                                .and_then(Value::as_str)
                                .unwrap_or("")
                        )
                    );
                }
            }
            Ok(0)
        }
        Command::Export { run, output } => {
            let bytes = client
                .basic_request(Method::GET, Some(*run), "/export", &[], None)
                .await
                .map_err(error)?;
            std::str::from_utf8(&bytes).map_err(|_| "Invalid Markdown response")?;
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(output)
                .map_err(|_| "Export destination exists or cannot be created")?;
            file.write_all(&bytes)
                .and_then(|_| file.sync_all())
                .map_err(|_| "Export write failed; destination may be partial")?;
            if cli.json {
                println!("{}", json!({"run_id":run,"exported":true}));
            } else {
                eprintln!("Export saved.");
            }
            Ok(0)
        }
        _ => Err("Unsupported command".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::DirBuilderExt;
    use tokio::{
        io::{AsyncReadExt, AsyncWriteExt},
        net::TcpListener,
    };

    #[tokio::test]
    async fn acknowledgement_failure_reports_accepted_submission() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let origin =
            BackendOrigin::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
        let run = Uuid::parse_str("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb").unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut headers = Vec::new();
            while !headers.ends_with(b"\r\n\r\n") {
                headers.push(stream.read_u8().await.unwrap());
            }
            let headers = String::from_utf8(headers).unwrap();
            assert!(headers.starts_with(&format!("PUT /api/basic-runs/{run} ")));
            let length = headers
                .lines()
                .find_map(|line| {
                    line.to_ascii_lowercase()
                        .strip_prefix("content-length: ")
                        .map(|s| s.parse::<usize>().unwrap())
                })
                .unwrap();
            let mut body = vec![0; length];
            stream.read_exact(&mut body).await.unwrap();
            let snapshot = json!({"run_id":run,"conversation_id":format!("cli-{run}"),"assistant_index":1,"lifecycle":"queued","cursor":"saved"}).to_string();
            stream
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                        snapshot.len(),
                        snapshot
                    )
                    .as_bytes(),
                )
                .await
                .unwrap();
        });
        let root = std::env::temp_dir().join(format!("council-ack-failure-{}", std::process::id()));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&root)
            .unwrap();
        let store = ReceiptStore::open(&root).unwrap();
        let mut client = ConfigClient::new(origin.clone(), "synthetic").unwrap();
        // A missing local record models an acknowledgment persistence failure.
        let failure = submit(
            &mut client,
            &origin,
            &store,
            run,
            json!({"content":"synthetic"}),
        )
        .await
        .unwrap_err();
        server.await.unwrap();
        std::fs::remove_dir_all(&root).unwrap();
        assert!(failure.contains("accepted"), "{failure}");
        assert!(failure.contains("resume"), "{failure}");
        assert!(!failure.contains("no new submission was sent"));
    }
}
