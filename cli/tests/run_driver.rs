use std::cell::Cell;
use std::collections::VecDeque;
use std::rc::Rc;

use council_cli::config::BackendOrigin;
use council_cli::monitor::MonitorError;
use council_cli::run_driver::{
    follow, reconnect_command, Control, DetachReason, OutputClosed, PendingInput, ProgressSink,
    ReadError, RunExit, SnapshotReader,
};
use council_cli::wire::{ResultReference, RetryNotice, RunLifecycle, RunSnapshot};
use uuid::Uuid;

fn waiting() -> RunSnapshot {
    serde_json::from_str(include_str!("../../tests/fixtures/cli/waiting.json")).unwrap()
}
fn state(lifecycle: RunLifecycle, cursor: &str) -> RunSnapshot {
    let mut snapshot = waiting();
    snapshot.lifecycle = lifecycle;
    snapshot.pending_prompt = None;
    snapshot.cursor = cursor.into();
    if snapshot.lifecycle == RunLifecycle::Complete {
        snapshot.result = Some(ResultReference {
            conversation_id: snapshot.conversation_id.clone(),
            assistant_index: snapshot.assistant_index.unwrap(),
            run_id: Some(snapshot.run_id),
        });
    }
    snapshot
}
fn origin() -> BackendOrigin {
    BackendOrigin::parse("http://127.0.0.1:8002").unwrap()
}
struct Reader {
    replies: VecDeque<Result<RunSnapshot, ReadError>>,
    calls: Vec<(Uuid, Option<String>)>,
    interrupt_on_read: Option<Rc<Cell<bool>>>,
}
impl Reader {
    fn new(replies: Vec<Result<RunSnapshot, ReadError>>) -> Self {
        Self {
            replies: replies.into(),
            calls: vec![],
            interrupt_on_read: None,
        }
    }
}
impl SnapshotReader for Reader {
    async fn snapshot<'a>(
        &'a mut self,
        run: Uuid,
        cursor: Option<&'a str>,
    ) -> Result<RunSnapshot, ReadError> {
        self.calls.push((run, cursor.map(str::to_owned)));
        if let Some(interrupt) = &self.interrupt_on_read {
            interrupt.set(true);
        }
        self.replies.pop_front().expect("unexpected extra read")
    }
}
struct Sink {
    interrupted: Rc<Cell<bool>>,
    result: Result<Control, OutputClosed>,
    seen: Vec<RunSnapshot>,
}
impl Sink {
    fn new() -> Self {
        Self {
            interrupted: Rc::new(Cell::new(false)),
            result: Ok(Control::Continue),
            seen: vec![],
        }
    }
}
impl ProgressSink for Sink {
    fn interrupted(&self) -> bool {
        self.interrupted.get()
    }
    fn progress(&mut self, snapshot: &RunSnapshot) -> Result<Control, OutputClosed> {
        self.seen.push(snapshot.clone());
        self.result
    }
}

#[tokio::test]
async fn polls_same_run_with_exact_cursors_then_returns_saved_result() {
    let done = state(RunLifecycle::Complete, "done");
    let run = done.run_id;
    let mut reader = Reader::new(vec![
        Ok(state(RunLifecycle::Queued, "opaque:/?a")),
        Ok(state(RunLifecycle::Running, "opaque:/?a")),
        Ok(state(RunLifecycle::Running, "next")),
        Ok(done.clone()),
    ]);
    let mut sink = Sink::new();
    let report = follow(&origin(), run, &mut reader, &mut sink, 10).await;
    assert_eq!(report.outcome, RunExit::Complete(done.result.unwrap()));
    assert_eq!(
        reader.calls,
        vec![
            (run, None),
            (run, Some("opaque:/?a".into())),
            (run, Some("opaque:/?a".into())),
            (run, Some("next".into()))
        ]
    );
    assert_eq!(sink.seen.len(), 4);
    assert_eq!(report.run_id, run);
    assert!(report.reconnect_command.contains("http://127.0.0.1:8002/"));
}

#[tokio::test]
async fn prompt_boundary_stops_without_accepting_defaults_or_skipping() {
    let snapshot = waiting();
    let mut reader = Reader::new(vec![Ok(snapshot.clone())]);
    let mut sink = Sink::new();
    let report = follow(&origin(), snapshot.run_id, &mut reader, &mut sink, 10).await;
    assert_eq!(
        report.outcome,
        RunExit::NeedsInput(PendingInput::Prompt(snapshot.pending_prompt.unwrap()))
    );
    assert_eq!(reader.calls.len(), 1);
}

#[tokio::test]
async fn uncertain_work_stops_at_exact_consent_notice_without_retry() {
    let mut snapshot = state(RunLifecycle::NeedsRetry, "retry");
    snapshot.retry = Some(RetryNotice {
        revision: "revision".into(),
        operation: "reviewer".into(),
        explanation: "May be billed again".into(),
    });
    let mut reader = Reader::new(vec![Ok(snapshot.clone())]);
    let report = follow(
        &origin(),
        snapshot.run_id,
        &mut reader,
        &mut Sink::new(),
        10,
    )
    .await;
    assert_eq!(
        report.outcome,
        RunExit::NeedsInput(PendingInput::RetryConsent(snapshot.retry.unwrap()))
    );
    assert_eq!(reader.calls.len(), 1);
}

#[tokio::test]
async fn mismatched_run_is_not_displayed_or_followed() {
    let mut reader = Reader::new(vec![Ok(waiting())]);
    let mut sink = Sink::new();
    let report = follow(&origin(), Uuid::nil(), &mut reader, &mut sink, 10).await;
    assert_eq!(
        report.outcome,
        RunExit::InvalidSnapshot(MonitorError::WrongRun)
    );
    assert!(sink.seen.is_empty());
    assert_eq!(reader.calls.len(), 1);
}

#[tokio::test]
async fn malformed_constructed_snapshot_is_not_displayed() {
    let mut snapshot = waiting();
    snapshot.pending_prompt = None;
    let mut reader = Reader::new(vec![Ok(snapshot.clone())]);
    let mut sink = Sink::new();
    let report = follow(&origin(), snapshot.run_id, &mut reader, &mut sink, 10).await;
    assert_eq!(
        report.outcome,
        RunExit::InvalidSnapshot(MonitorError::InvalidSnapshot)
    );
    assert!(sink.seen.is_empty());
}

#[tokio::test]
async fn every_read_failure_stops_without_retry() {
    for error in [
        ReadError::Authentication,
        ReadError::Unavailable,
        ReadError::Timeout,
        ReadError::InvalidResponse,
    ] {
        let mut reader = Reader::new(vec![Err(error)]);
        let mut sink = Sink::new();
        let report = follow(&origin(), waiting().run_id, &mut reader, &mut sink, 10).await;
        assert_eq!(report.outcome, RunExit::ReadFailed(error));
        assert_eq!(reader.calls.len(), 1);
        assert!(sink.seen.is_empty());
    }
}

#[tokio::test]
async fn broken_output_or_requested_detach_does_not_read_again() {
    for (result, reason) in [
        (Err(OutputClosed), DetachReason::OutputClosed),
        (Ok(Control::Detach), DetachReason::Requested),
    ] {
        let snapshot = state(RunLifecycle::Running, "cursor");
        let mut reader = Reader::new(vec![Ok(snapshot.clone())]);
        let mut sink = Sink::new();
        sink.result = result;
        let report = follow(&origin(), snapshot.run_id, &mut reader, &mut sink, 10).await;
        assert_eq!(report.outcome, RunExit::Detached(reason));
        assert_eq!(reader.calls.len(), 1);
    }
}

#[tokio::test]
async fn interruption_before_read_performs_no_io() {
    let mut reader = Reader::new(vec![]);
    let mut sink = Sink::new();
    sink.interrupted.set(true);
    let report = follow(&origin(), waiting().run_id, &mut reader, &mut sink, 10).await;
    assert_eq!(report.outcome, RunExit::Detached(DetachReason::Interrupted));
    assert!(reader.calls.is_empty());
    assert!(sink.seen.is_empty());
}

#[tokio::test]
async fn interruption_during_read_does_not_display_or_act_on_reply() {
    let mut reader = Reader::new(vec![Ok(waiting())]);
    let mut sink = Sink::new();
    reader.interrupt_on_read = Some(sink.interrupted.clone());
    let report = follow(&origin(), waiting().run_id, &mut reader, &mut sink, 10).await;
    assert_eq!(report.outcome, RunExit::Detached(DetachReason::Interrupted));
    assert!(sink.seen.is_empty());
}

#[tokio::test]
async fn observation_budget_detaches_without_cancelling() {
    for budget in [0, 2] {
        let snapshot = state(RunLifecycle::Running, "unchanged");
        let mut reader = Reader::new(vec![Ok(snapshot.clone()); budget]);
        let report = follow(
            &origin(),
            snapshot.run_id,
            &mut reader,
            &mut Sink::new(),
            budget,
        )
        .await;
        assert_eq!(
            report.outcome,
            RunExit::Detached(DetachReason::ObservationLimit)
        );
        assert_eq!(reader.calls.len(), budget);
    }
}

#[tokio::test]
async fn cancellation_pending_polls_but_cancelled_and_failed_stop() {
    let run = waiting().run_id;
    let mut reader = Reader::new(vec![
        Ok(state(RunLifecycle::Cancelling, "stopping")),
        Ok(state(RunLifecycle::Cancelled, "stopped")),
    ]);
    let report = follow(&origin(), run, &mut reader, &mut Sink::new(), 10).await;
    assert_eq!(report.outcome, RunExit::Cancelled);
    assert_eq!(reader.calls.len(), 2);
    let mut failed = state(RunLifecycle::Failed, "failed");
    failed.error = Some("raw error\u{1b}[2J".into());
    let mut reader = Reader::new(vec![Ok(failed.clone())]);
    let report = follow(&origin(), run, &mut reader, &mut Sink::new(), 10).await;
    assert_eq!(report.outcome, RunExit::Failed(failed.error));
    assert_eq!(reader.calls.len(), 1);
}

#[test]
fn reconnect_retains_origin_and_run_without_shell_globbing_ipv6() {
    let run = waiting().run_id;
    let origin = BackendOrigin::parse("https://[::1]:8443").unwrap();
    assert_eq!(
        reconnect_command(&origin, run),
        format!("council --backend-url 'https://[::1]:8443/' resume {run}")
    );
}
