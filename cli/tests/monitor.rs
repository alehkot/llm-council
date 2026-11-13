use council_cli::monitor::{observe, MonitorAction, MonitorError, NeedsInput};
use council_cli::wire::{Identifier, ResultReference, RetryNotice, RunLifecycle, RunSnapshot};
use uuid::Uuid;

fn waiting() -> RunSnapshot {
    serde_json::from_str(include_str!("../../tests/fixtures/cli/waiting.json")).unwrap()
}

fn state(lifecycle: RunLifecycle) -> RunSnapshot {
    let mut snapshot = waiting();
    snapshot.lifecycle = lifecycle;
    snapshot.pending_prompt = None;
    snapshot
}

fn retry() -> RunSnapshot {
    let mut snapshot = state(RunLifecycle::NeedsRetry);
    snapshot.retry = Some(RetryNotice {
        revision: "opaque/revision:1".into(),
        operation: "stage2 reviewer".into(),
        explanation: "The outcome is unknown; another request may be billed.".into(),
    });
    snapshot
}

fn complete() -> RunSnapshot {
    let mut snapshot = state(RunLifecycle::Complete);
    snapshot.result = Some(ResultReference {
        conversation_id: snapshot.conversation_id.clone(),
        assistant_index: snapshot.assistant_index.unwrap(),
        run_id: Some(snapshot.run_id),
    });
    snapshot
}

#[test]
fn active_states_poll_without_interpreting_or_changing_the_cursor() {
    for lifecycle in [
        RunLifecycle::Queued,
        RunLifecycle::Running,
        RunLifecycle::Cancelling,
    ] {
        let mut snapshot = state(lifecycle);
        snapshot.cursor = "opaque: /?not-a-url\n".into();
        let before = snapshot.clone();
        for can_prompt in [false, true] {
            assert_eq!(
                observe(snapshot.run_id, &snapshot, can_prompt),
                Ok(MonitorAction::Poll {
                    cursor: &snapshot.cursor
                })
            );
        }
        assert_eq!(snapshot, before);
    }
}

#[test]
fn interactive_wait_returns_the_exact_instance_and_all_field_types() {
    let snapshot = waiting();
    let before = snapshot.clone();
    let prompt = snapshot.pending_prompt.as_ref().unwrap();
    assert_eq!(prompt.prompt.fields.len(), 4);
    assert_eq!(
        observe(snapshot.run_id, &snapshot, true),
        Ok(MonitorAction::Prompt(prompt))
    );
    assert_eq!(snapshot, before);
}

#[test]
fn noninteractive_wait_neither_skips_nor_accepts_defaults() {
    let snapshot = waiting();
    let prompt = snapshot.pending_prompt.as_ref().unwrap();
    assert!(!prompt.prompt.skip_allowed);
    assert_eq!(
        observe(snapshot.run_id, &snapshot, false),
        Ok(MonitorAction::NeedsInput(NeedsInput::Prompt(prompt)))
    );
}

#[test]
fn retry_is_only_a_consent_request_even_on_repeated_observation() {
    let snapshot = retry();
    let before = snapshot.clone();
    let notice = snapshot.retry.as_ref().unwrap();
    for _ in 0..3 {
        assert_eq!(
            observe(snapshot.run_id, &snapshot, true),
            Ok(MonitorAction::RequestRetryConsent(notice))
        );
        assert_eq!(
            observe(snapshot.run_id, &snapshot, false),
            Ok(MonitorAction::NeedsInput(NeedsInput::RetryConsent(notice)))
        );
    }
    assert_eq!(snapshot, before);
}

#[test]
fn complete_returns_saved_reference_not_a_fabricated_answer() {
    let snapshot = complete();
    assert!(snapshot.stages.is_empty());
    assert_eq!(
        observe(snapshot.run_id, &snapshot, false),
        Ok(MonitorAction::Complete(snapshot.result.as_ref().unwrap()))
    );
}

#[test]
fn failed_and_cancelled_are_terminal_not_poll_or_retry() {
    let mut snapshot = state(RunLifecycle::Failed);
    snapshot.error = Some("provider error\u{1b}[2J".into());
    assert_eq!(
        observe(snapshot.run_id, &snapshot, true),
        Ok(MonitorAction::Failed {
            error: snapshot.error.as_deref()
        })
    );
    snapshot.error = None;
    assert_eq!(
        observe(snapshot.run_id, &snapshot, false),
        Ok(MonitorAction::Failed { error: None })
    );
    snapshot.lifecycle = RunLifecycle::Cancelled;
    assert_eq!(
        observe(snapshot.run_id, &snapshot, true),
        Ok(MonitorAction::Cancelled)
    );
}

#[test]
fn wrong_run_is_rejected_before_any_action() {
    let snapshot = waiting();
    assert_eq!(
        observe(Uuid::nil(), &snapshot, true),
        Err(MonitorError::WrongRun)
    );
}

#[test]
fn directly_constructed_cross_run_prompt_is_rejected() {
    let mut snapshot = waiting();
    snapshot.pending_prompt.as_mut().unwrap().run_id = Uuid::nil();
    assert_eq!(
        observe(snapshot.run_id, &snapshot, true),
        Err(MonitorError::InvalidSnapshot)
    );
}

#[test]
fn missing_or_unexpected_prompt_is_rejected() {
    let mut snapshot = waiting();
    snapshot.lifecycle = RunLifecycle::Running;
    assert_eq!(
        observe(snapshot.run_id, &snapshot, true),
        Err(MonitorError::InvalidSnapshot)
    );
    snapshot.pending_prompt = None;
    snapshot.lifecycle = RunLifecycle::WaitingInput;
    assert_eq!(
        observe(snapshot.run_id, &snapshot, false),
        Err(MonitorError::InvalidSnapshot)
    );
}

#[test]
fn missing_or_unexpected_retry_notice_is_rejected() {
    let mut snapshot = retry();
    snapshot.lifecycle = RunLifecycle::Running;
    assert_eq!(
        observe(snapshot.run_id, &snapshot, true),
        Err(MonitorError::InvalidSnapshot)
    );
    snapshot.retry = None;
    snapshot.lifecycle = RunLifecycle::NeedsRetry;
    assert_eq!(
        observe(snapshot.run_id, &snapshot, false),
        Err(MonitorError::InvalidSnapshot)
    );
}

#[test]
fn directly_constructed_empty_retry_fields_are_rejected() {
    for index in 0..3 {
        let mut snapshot = retry();
        let notice = snapshot.retry.as_mut().unwrap();
        match index {
            0 => notice.revision.clear(),
            1 => notice.operation.clear(),
            _ => notice.explanation.clear(),
        }
        assert_eq!(
            observe(snapshot.run_id, &snapshot, true),
            Err(MonitorError::InvalidSnapshot)
        );
    }
}

#[test]
fn complete_requires_a_result_reference() {
    let snapshot = state(RunLifecycle::Complete);
    assert_eq!(
        observe(snapshot.run_id, &snapshot, true),
        Err(MonitorError::InvalidSnapshot)
    );
}

#[test]
fn result_reference_must_match_run_conversation_and_message() {
    for index in 0..4 {
        let mut snapshot = complete();
        let result = snapshot.result.as_mut().unwrap();
        match index {
            0 => result.run_id = None,
            1 => result.run_id = Some(Uuid::nil()),
            2 => result.conversation_id = Identifier::new("other".into()).unwrap(),
            _ => result.assistant_index += 1,
        }
        assert_eq!(
            observe(snapshot.run_id, &snapshot, true),
            Err(MonitorError::InvalidSnapshot)
        );
    }
}
