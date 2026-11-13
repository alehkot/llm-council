//! Minimal, unwired single-run read loop for future `ask`/`resume` commands.
//!
//! There is no HTTP implementation or mutation interface. Prompt answers, retry
//! consent, submission, cancellation, result rendering and OS signal handling are
//! not implemented here. Dropping this future only detaches the caller; this
//! driver has no operation that can cancel backend work.

use std::future::Future;

use crate::config::BackendOrigin;
use crate::monitor::{observe, MonitorAction, MonitorError, NeedsInput};
use crate::wire::{PromptInstance, ResultReference, RetryNotice, RunSnapshot};
use uuid::Uuid;

/// Sanitized read failures: never retain transport bodies, URLs or credentials.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReadError {
    Authentication,
    Unavailable,
    Timeout,
    InvalidResponse,
}

/// A future transport must authenticate and establish API1 support before this
/// loop is entered. The caller must bind this reader to the same backend origin
/// supplied to `follow`; the injected loop cannot verify the reader's origin.
/// Reads must be bounded long polls, not immediate busy polls.
/// No read error is retried here, including authentication and lockout failures.
pub trait SnapshotReader {
    fn snapshot<'a>(
        &'a mut self,
        run: Uuid,
        cursor: Option<&'a str>,
    ) -> impl Future<Output = Result<RunSnapshot, ReadError>> + 'a;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Control {
    Continue,
    Detach,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct OutputClosed;

/// Injected progress/interrupt boundary, not a real terminal implementation.
/// Escape untrusted snapshot text before display. Progress belongs on stderr;
/// final-answer stdout and JSON output are outside this read loop.
pub trait ProgressSink {
    fn interrupted(&self) -> bool;
    fn progress(&mut self, snapshot: &RunSnapshot) -> Result<Control, OutputClosed>;
}

#[derive(Debug, PartialEq, Eq)]
pub enum PendingInput {
    Prompt(PromptInstance),
    RetryConsent(RetryNotice),
}

#[derive(Debug, PartialEq, Eq)]
pub enum DetachReason {
    Interrupted,
    OutputClosed,
    Requested,
    ObservationLimit,
}

#[derive(Debug, PartialEq, Eq)]
pub enum RunExit {
    Complete(ResultReference),
    Failed(Option<String>),
    Cancelled,
    NeedsInput(PendingInput),
    Detached(DetachReason),
    ReadFailed(ReadError),
    InvalidSnapshot(MonitorError),
}

#[derive(Debug, PartialEq, Eq)]
pub struct RunReport {
    pub run_id: Uuid,
    /// A reconnect hint, not a claim that the currently gated command works.
    pub reconnect_command: String,
    pub outcome: RunExit,
}

/// Retain the validated origin, including a nondefault port or IPv6 literal.
/// BackendOrigin excludes credentials, paths, query strings and shell quotes;
/// quoting the URL additionally prevents IPv6 brackets acting as shell globs.
pub fn reconnect_command(origin: &BackendOrigin, run: Uuid) -> String {
    format!("council --backend-url '{}' resume {run}", origin.as_url())
}

/// Follow at most `max_observations` snapshots of exactly one run. Stop at every
/// human-input boundary, including in interactive callers: UI/answer submission
/// must be connected separately once durable mutation receipts are available.
/// The budget is a request-count bound, not a timeout or cancellation request.
/// Interrupts are checked before and after each read; aborting an in-flight read
/// promptly requires the future signal/transport driver to drop this future.
pub async fn follow(
    origin: &BackendOrigin,
    run: Uuid,
    reader: &mut impl SnapshotReader,
    sink: &mut impl ProgressSink,
    max_observations: usize,
) -> RunReport {
    let outcome = follow_inner(run, reader, sink, max_observations).await;
    RunReport {
        run_id: run,
        reconnect_command: reconnect_command(origin, run),
        outcome,
    }
}

async fn follow_inner(
    run: Uuid,
    reader: &mut impl SnapshotReader,
    sink: &mut impl ProgressSink,
    max_observations: usize,
) -> RunExit {
    let mut cursor = None;
    for _ in 0..max_observations {
        if sink.interrupted() {
            return RunExit::Detached(DetachReason::Interrupted);
        }
        let snapshot = match reader.snapshot(run, cursor.as_deref()).await {
            Ok(snapshot) => snapshot,
            Err(error) => return RunExit::ReadFailed(error),
        };
        if sink.interrupted() {
            return RunExit::Detached(DetachReason::Interrupted);
        }
        let action = match observe(run, &snapshot, false) {
            Ok(action) => action,
            Err(error) => return RunExit::InvalidSnapshot(error),
        };
        match sink.progress(&snapshot) {
            Ok(Control::Continue) => {}
            Ok(Control::Detach) => return RunExit::Detached(DetachReason::Requested),
            Err(OutputClosed) => return RunExit::Detached(DetachReason::OutputClosed),
        }
        match action {
            MonitorAction::Poll { cursor: next } => cursor = Some(next.to_owned()),
            MonitorAction::NeedsInput(NeedsInput::Prompt(prompt))
            | MonitorAction::Prompt(prompt) => {
                return RunExit::NeedsInput(PendingInput::Prompt(prompt.clone()));
            }
            MonitorAction::NeedsInput(NeedsInput::RetryConsent(notice))
            | MonitorAction::RequestRetryConsent(notice) => {
                return RunExit::NeedsInput(PendingInput::RetryConsent(notice.clone()));
            }
            MonitorAction::Complete(result) => return RunExit::Complete(result.clone()),
            MonitorAction::Failed { error } => return RunExit::Failed(error.map(str::to_owned)),
            MonitorAction::Cancelled => return RunExit::Cancelled,
        }
    }
    RunExit::Detached(DetachReason::ObservationLimit)
}
