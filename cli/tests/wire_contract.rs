use council_cli::wire::{
    AnswerRequest, AnswerValue, CreateRunRequest, FieldType, Identifier, PromptSchema,
    ResultReference, RetryRequest, RunLifecycle, RunReceipt, RunSnapshot,
};
use serde_json::{json, Value};

const CREATE: &str = include_str!("../../tests/fixtures/cli/create.json");
const WAITING: &str = include_str!("../../tests/fixtures/cli/waiting.json");
const ANSWERS: &str = include_str!("../../tests/fixtures/cli/answers.json");
const LEGACY: &str = include_str!("../../tests/fixtures/cli/legacy-conversation.json");

fn minimal_create() -> Value {
    json!({"content": "question", "interaction_policy": "persistent", "recovery_policy": "explicit_retry"})
}

fn waiting() -> Value {
    serde_json::from_str(WAITING).unwrap()
}

fn queued() -> Value {
    let mut value = waiting();
    value["lifecycle"] = json!("queued");
    value.as_object_mut().unwrap().remove("pending_prompt");
    value
}

#[test]
fn create_fixture_preserves_exact_text_and_policies() {
    let request: CreateRunRequest = serde_json::from_str(CREATE).unwrap();
    request.validate_limits(1000, 1000, 10).unwrap();
    assert_eq!(
        serde_json::to_value(&request).unwrap(),
        serde_json::from_str::<Value>(CREATE).unwrap()
    );
    assert!(request.content.starts_with("  "));
    assert!(request.refine);
}

#[test]
fn create_defaults_match_materialized_backend_request() {
    let request: CreateRunRequest = serde_json::from_value(minimal_create()).unwrap();
    let mut expected = minimal_create();
    expected["negative_prompt"] = Value::Null;
    expected["skill_ids"] = json!([]);
    expected["refine"] = json!(false);
    assert_eq!(serde_json::to_value(request).unwrap(), expected);
}

#[test]
fn create_requires_explicit_safe_policies_and_known_fields() {
    for field in ["interaction_policy", "recovery_policy"] {
        let mut value = minimal_create();
        value.as_object_mut().unwrap().remove(field);
        assert!(serde_json::from_value::<CreateRunRequest>(value).is_err());
        let mut value = minimal_create();
        value[field] = json!("legacy");
        assert!(serde_json::from_value::<CreateRunRequest>(value).is_err());
    }
    let mut value = minimal_create();
    value["model"] = json!("not-allowed");
    assert!(serde_json::from_value::<CreateRunRequest>(value).is_err());
}

#[test]
fn create_does_not_coerce_primitives_or_null_defaults() {
    for (field, invalid) in [
        ("content", json!(123)),
        ("refine", json!(1)),
        ("refine", json!("true")),
        ("refine", Value::Null),
        ("skill_ids", Value::Null),
        ("negative_prompt", json!(false)),
    ] {
        let mut value = minimal_create();
        value[field] = invalid;
        assert!(
            serde_json::from_value::<CreateRunRequest>(value).is_err(),
            "{field}"
        );
    }
}

#[test]
fn empty_questions_and_duplicate_skills_are_rejected_without_trimming_text() {
    for text in ["", " \t\n", "\u{a0}\u{2003}", "\u{1c}\u{1f}"] {
        let mut value = minimal_create();
        value["content"] = json!(text);
        assert!(serde_json::from_value::<CreateRunRequest>(value).is_err());
    }
    let mut value = minimal_create();
    value["skill_ids"] = json!(["one", "one"]);
    assert!(serde_json::from_value::<CreateRunRequest>(value).is_err());
}

#[test]
fn character_limits_count_unicode_scalars_not_bytes_or_graphemes() {
    let mut value = minimal_create();
    value["content"] = json!("🦫яe\u{301}");
    value["negative_prompt"] = json!("🦫я");
    value["skill_ids"] = json!(["one", "two"]);
    let request: CreateRunRequest = serde_json::from_value(value).unwrap();
    assert!(request.validate_limits(4, 2, 2).is_ok());
    assert!(request.validate_limits(3, 2, 2).is_err());
    assert!(request.validate_limits(4, 1, 2).is_err());
    assert!(request.validate_limits(4, 2, 1).is_err());
}

#[test]
fn identifiers_match_backend_ascii_shape() {
    for invalid in ["", "a b", "../x", "with.dot", "русский"] {
        assert!(Identifier::new(invalid.into()).is_err());
    }
    assert!(Identifier::new("a".repeat(129)).is_err());
    assert!(Identifier::new("a".repeat(128)).is_ok());
    assert_eq!(
        Identifier::new("skill_A-9".into()).unwrap().as_str(),
        "skill_A-9"
    );
    assert!(serde_json::from_value::<Identifier>(json!(5)).is_err());
}

#[test]
fn waiting_fixture_preserves_four_field_types_and_false_default() {
    let snapshot: RunSnapshot = serde_json::from_str(WAITING).unwrap();
    let fields = &snapshot.pending_prompt.as_ref().unwrap().prompt.fields;
    assert_eq!(
        fields.iter().map(|f| &f.field_type).collect::<Vec<_>>(),
        vec![
            &FieldType::Select,
            &FieldType::MultiSelect,
            &FieldType::Text,
            &FieldType::Checkbox,
        ]
    );
    assert_eq!(fields[3].default, Some(AnswerValue::Boolean(false)));
    assert_eq!(snapshot.lifecycle, RunLifecycle::WaitingInput);
    let roundtrip: RunSnapshot =
        serde_json::from_value(serde_json::to_value(&snapshot).unwrap()).unwrap();
    assert_eq!(snapshot, roundtrip);
}

#[test]
fn unknown_nested_fields_and_prompt_types_are_rejected() {
    let mut value = waiting();
    value["pending_prompt"]["prompt"]["fields"][0]["extra"] = json!(true);
    assert!(serde_json::from_value::<RunSnapshot>(value).is_err());
    let mut value = waiting();
    value["pending_prompt"]["prompt"]["fields"][0]["type"] = json!("slider");
    assert!(serde_json::from_value::<RunSnapshot>(value).is_err());
    let mut value = waiting();
    value["pending_prompt"]["prompt"]["type"] = json!("other");
    assert!(serde_json::from_value::<RunSnapshot>(value).is_err());
    let mut value = waiting();
    value["extra"] = json!({});
    assert!(serde_json::from_value::<RunSnapshot>(value).is_err());
}

#[test]
fn prompt_defaults_do_not_create_answers() {
    let prompt: PromptSchema = serde_json::from_value(json!({
        "prompt_id":"test", "prompt_type":"pre_query", "title":"title", "description":"",
        "fields":[{"id":"field", "type":"text", "label":"Label"}]
    }))
    .unwrap();
    assert!(prompt.skip_allowed);
    assert!(!prompt.fields[0].required);
    assert!(prompt.fields[0].default.is_none());
    assert!(prompt.fields[0].options.is_empty());
    let answer: AnswerRequest = serde_json::from_value(json!({})).unwrap();
    assert!(answer.responses.is_empty());
    assert!(!answer.skipped);
}

#[test]
fn answers_fixture_preserves_false_unicode_escape_and_list_order() {
    let answer: AnswerRequest = serde_json::from_str(ANSWERS).unwrap();
    assert_eq!(answer.responses["confirmed"], AnswerValue::Boolean(false));
    assert_eq!(
        serde_json::to_value(answer).unwrap(),
        serde_json::from_str::<Value>(ANSWERS).unwrap()
    );
}

#[test]
fn answer_values_reject_numbers_objects_null_and_mixed_lists() {
    for invalid in [
        json!(1),
        json!(1.5),
        Value::Null,
        json!({}),
        json!(["a", true]),
        json!([1]),
    ] {
        assert!(
            serde_json::from_value::<AnswerRequest>(json!({"responses":{"field":invalid}}))
                .is_err()
        );
    }
    for invalid in [json!(1), json!("false"), Value::Null] {
        assert!(serde_json::from_value::<AnswerRequest>(json!({"skipped":invalid})).is_err());
    }
}

#[test]
fn skipped_answers_cannot_contain_responses() {
    assert!(serde_json::from_value::<AnswerRequest>(
        json!({"responses":{"a":false},"skipped":true})
    )
    .is_err());
    assert!(serde_json::from_value::<AnswerRequest>(json!({"skipped":true})).is_ok());
    assert!(serde_json::from_value::<AnswerRequest>(json!({"extra":true})).is_err());
}

#[test]
fn waiting_ownership_and_presence_are_enforced() {
    let mut value = waiting();
    value["pending_prompt"] = Value::Null;
    assert!(serde_json::from_value::<RunSnapshot>(value).is_err());
    let mut value = waiting();
    value["pending_prompt"]["run_id"] = json!("33333333-3333-4333-8333-333333333333");
    assert!(serde_json::from_value::<RunSnapshot>(value).is_err());
    let mut value = waiting();
    value["lifecycle"] = json!("running");
    assert!(serde_json::from_value::<RunSnapshot>(value).is_err());
}

#[test]
fn retry_state_requires_a_nonempty_notice_and_other_states_forbid_it() {
    let mut value = queued();
    value["lifecycle"] = json!("needs_retry");
    assert!(serde_json::from_value::<RunSnapshot>(value.clone()).is_err());
    value["retry"] =
        json!({"revision":"opaque", "operation":"stage1", "explanation":"Uncertain work"});
    assert!(serde_json::from_value::<RunSnapshot>(value.clone()).is_ok());
    for field in ["revision", "operation", "explanation"] {
        let mut broken = value.clone();
        broken["retry"][field] = json!("");
        assert!(serde_json::from_value::<RunSnapshot>(broken).is_err());
    }
    value["lifecycle"] = json!("queued");
    assert!(serde_json::from_value::<RunSnapshot>(value).is_err());
    assert!(serde_json::from_value::<RetryRequest>(json!({"revision":""})).is_err());
    assert!(serde_json::from_value::<RetryRequest>(json!({"revision":10})).is_err());
}

#[test]
fn complete_state_requires_exact_run_conversation_and_message_reference() {
    let mut value = queued();
    value["lifecycle"] = json!("complete");
    assert!(serde_json::from_value::<RunSnapshot>(value.clone()).is_err());
    value["result"] = json!({"run_id":value["run_id"], "conversation_id":value["conversation_id"], "assistant_index":1});
    assert!(serde_json::from_value::<RunSnapshot>(value.clone()).is_ok());
    for (field, wrong) in [
        ("run_id", Value::Null),
        ("conversation_id", json!("another")),
        ("assistant_index", json!(2)),
    ] {
        let mut broken = value.clone();
        broken["result"][field] = wrong;
        assert!(serde_json::from_value::<RunSnapshot>(broken).is_err());
    }
}

#[test]
fn indices_uuids_and_snapshot_maps_are_strict() {
    for index in [json!(-1), json!(true), json!(1.0), json!("1")] {
        let mut value = queued();
        value["assistant_index"] = index;
        assert!(serde_json::from_value::<RunSnapshot>(value).is_err());
    }
    let mut value = queued();
    value["run_id"] = json!("not-a-uuid");
    assert!(serde_json::from_value::<RunSnapshot>(value).is_err());
    for field in ["stages", "provenance"] {
        let mut value = queued();
        value[field] = Value::Null;
        assert!(serde_json::from_value::<RunSnapshot>(value).is_err());
    }
}

#[test]
fn heterogeneous_stage_evidence_and_original_text_roundtrip_unchanged() {
    let mut value = queued();
    value["stages"] =
        json!({"stage1":[{"text":"\u{1b}[31m русский 🦫", "usage":{"x":2}, "extra":[false,null]}]});
    value["provenance"] = json!({"provider":{"unknown":"preserved"}});
    let snapshot: RunSnapshot = serde_json::from_value(value.clone()).unwrap();
    let actual = serde_json::to_value(snapshot).unwrap();
    assert_eq!(actual["stages"], value["stages"]);
    assert_eq!(actual["provenance"], value["provenance"]);
}

#[test]
fn legacy_export_reference_has_no_fabricated_run_id() {
    let conversation: Value = serde_json::from_str(LEGACY).unwrap();
    let reference: ResultReference = serde_json::from_value(json!({
        "conversation_id": conversation["id"], "assistant_index":1,
    }))
    .unwrap();
    assert!(reference.run_id.is_none());
    assert_eq!(reference.conversation_id.as_str(), "legacy-conversation");
    assert_eq!(
        serde_json::to_value(reference).unwrap()["run_id"],
        Value::Null
    );
    // Legacy content remains backend-owned: deriving an export reference never normalizes it.
    assert_eq!(
        conversation["messages"][1]["stage3"]["timestamp"],
        "2020-01-02T03:05:06"
    );
}

#[test]
fn queued_receipt_may_omit_message_index_but_not_identity_or_lifecycle() {
    let receipt: RunReceipt = serde_json::from_value(json!({
        "run_id":"11111111-1111-4111-8111-111111111111",
        "conversation_id":"conversation", "lifecycle":"queued"
    }))
    .unwrap();
    assert!(receipt.assistant_index.is_none());
    assert!(serde_json::from_value::<RunReceipt>(
        json!({"conversation_id":"conversation", "lifecycle":"queued"})
    )
    .is_err());
}
