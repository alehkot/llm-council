//! Offline decision core shared by the future `ask` and `resume` commands.
//!
//! This module performs no I/O, sleeps, submission, prompt answering, retry or
//! cancellation. A transport/terminal driver must implement the returned action
//! only after the public durable backend contract is available. In particular,
//! observing `needs_retry` is never consent to repeat provider work.
//! A driver leaving on `NeedsInput` must print the expected run ID and a
//! reconnect command retaining the configured backend origin. Ctrl-C or a
//! broken output pipe must detach the driver, not invoke cancellation. These
//! terminal/network mechanics are intentionally not implemented by this core.

use crate::wire::{PromptInstance, ResultReference, RetryNotice, RunLifecycle, RunSnapshot};
use uuid::Uuid;

#[derive(Debug, PartialEq, Eq)]
pub enum MonitorError {
    WrongRun,
    InvalidSnapshot,
}

#[derive(Debug, PartialEq, Eq)]
pub enum NeedsInput<'a> {
    Prompt(&'a PromptInstance),
    RetryConsent(&'a RetryNotice),
}

/// All borrowed text is untrusted. Apply display escaping at the terminal
/// boundary, not to the snapshot or the data later exported/saved.
#[derive(Debug, PartialEq, Eq)]
pub enum MonitorAction<'a> {
    /// Issue a safe read for this same run using the opaque cursor unchanged.
    Poll { cursor: &'a str },
    /// Present this exact instance; do not accept defaults without user input.
    Prompt(&'a PromptInstance),
    /// Ask explicit consent for this exact revision before sending any retry.
    RequestRetryConsent(&'a RetryNotice),
    /// Exit with needs-input status (3), preserving the run for later resume.
    NeedsInput(NeedsInput<'a>),
    /// Fetch/render the referenced saved result. This is not its answer text.
    Complete(&'a ResultReference),
    Failed { error: Option<&'a str> },
    Cancelled,
}

/// Decide one observation, never advance backend state. Both noninteractive
/// mode and absence of a controlling terminal must set `can_prompt` to false.
/// Repeated snapshots return the same decision; the driver owns user input and
/// submission acknowledgment, and must not use repeated observations to replay it.
pub fn observe(
    expected_run: Uuid,
    snapshot: &RunSnapshot,
    can_prompt: bool,
) -> Result<MonitorAction<'_>, MonitorError> {
    if snapshot.run_id != expected_run {
        return Err(MonitorError::WrongRun);
    }
    // Wire decoding checks these too. Keep this boundary safe for snapshots
    // constructed directly by tests or an in-process caller.
    if (snapshot.lifecycle == RunLifecycle::WaitingInput) != snapshot.pending_prompt.is_some()
        || (snapshot.lifecycle == RunLifecycle::NeedsRetry) != snapshot.retry.is_some()
        || snapshot
            .pending_prompt
            .as_ref()
            .is_some_and(|prompt| prompt.run_id != expected_run)
        || snapshot.result.as_ref().is_some_and(|result| {
            result.run_id != Some(expected_run)
                || result.conversation_id != snapshot.conversation_id
                || Some(result.assistant_index) != snapshot.assistant_index
        })
    {
        return Err(MonitorError::InvalidSnapshot);
    }
    match snapshot.lifecycle {
        RunLifecycle::Queued | RunLifecycle::Running | RunLifecycle::Cancelling => {
            Ok(MonitorAction::Poll {
                cursor: &snapshot.cursor,
            })
        }
        RunLifecycle::WaitingInput => {
            let prompt = snapshot
                .pending_prompt
                .as_ref()
                .ok_or(MonitorError::InvalidSnapshot)?;
            Ok(if can_prompt {
                MonitorAction::Prompt(prompt)
            } else {
                MonitorAction::NeedsInput(NeedsInput::Prompt(prompt))
            })
        }
        RunLifecycle::NeedsRetry => {
            let retry = snapshot
                .retry
                .as_ref()
                .filter(|notice| {
                    !notice.revision.is_empty()
                        && !notice.operation.is_empty()
                        && !notice.explanation.is_empty()
                })
                .ok_or(MonitorError::InvalidSnapshot)?;
            Ok(if can_prompt {
                MonitorAction::RequestRetryConsent(retry)
            } else {
                MonitorAction::NeedsInput(NeedsInput::RetryConsent(retry))
            })
        }
        RunLifecycle::Complete => snapshot
            .result
            .as_ref()
            .map(MonitorAction::Complete)
            .ok_or(MonitorError::InvalidSnapshot),
        RunLifecycle::Failed => Ok(MonitorAction::Failed {
            error: snapshot.error.as_deref(),
        }),
        RunLifecycle::Cancelled => Ok(MonitorAction::Cancelled),
    }
}
