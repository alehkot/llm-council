use council_cli::prompt::*;
use council_cli::wire::{AnswerValue, FieldType, PromptInstance};
use std::collections::VecDeque;
use uuid::Uuid;
fn fixture() -> PromptInstance {
    serde_json::from_str::<council_cli::wire::RunSnapshot>(include_str!(
        "../../tests/fixtures/cli/waiting.json"
    ))
    .unwrap()
    .pending_prompt
    .unwrap()
}
fn limits() -> Limits {
    Limits {
        fields: 16,
        options_per_field: 32,
        text_chars: 1000,
    }
}
struct Terminal {
    begin: Option<PromptChoice>,
    choices: VecDeque<FieldChoice>,
    calls: usize,
    views: Vec<FieldView>,
    header: Option<PromptView>,
}
impl Terminal {
    fn new(choices: Vec<FieldChoice>) -> Self {
        Self {
            begin: Some(PromptChoice::Answer),
            choices: choices.into(),
            calls: 0,
            views: vec![],
            header: None,
        }
    }
}
impl PromptTerminal for Terminal {
    fn begin(&mut self, view: &PromptView) -> Result<PromptChoice, CollectError> {
        self.calls += 1;
        self.header = Some(PromptView {
            title: view.title.clone(),
            description: view.description.clone(),
            skip_allowed: view.skip_allowed,
        });
        Ok(self.begin.take().unwrap())
    }
    fn field(&mut self, view: &FieldView, _max: usize) -> Result<FieldChoice, CollectError> {
        self.calls += 1;
        self.views.push(FieldView {
            field_type: view.field_type.clone(),
            label: view.label.clone(),
            description: view.description.clone(),
            required: view.required,
            options: view.options.clone(),
            default: view.default.clone(),
        });
        self.choices.pop_front().ok_or(CollectError::Terminal)
    }
}
fn run(p: &PromptInstance, t: &mut Terminal) -> Result<Collected, CollectError> {
    collect(p.run_id, p, true, limits(), t)
}
fn single(index: usize) -> PromptInstance {
    let mut p = fixture();
    p.prompt.fields = vec![p.prompt.fields[index].clone()];
    p
}
#[test]
fn all_fields_preserve_exact_values_false_and_selection_order() {
    let p = fixture();
    let mut t = Terminal::new(vec![
        FieldChoice::Select(0),
        FieldChoice::MultiSelect(vec![0]),
        FieldChoice::Text("  Привет\n\u{1b}[2J".into()),
        FieldChoice::Checkbox(false),
    ]);
    let Collected::Answer {
        run_id,
        instance_id,
        answer,
    } = run(&p, &mut t).unwrap()
    else {
        panic!()
    };
    assert_eq!(run_id, p.run_id);
    assert_eq!(instance_id, p.instance_id);
    assert!(!answer.skipped);
    assert_eq!(
        answer.responses["choice"],
        AnswerValue::Text("first".into())
    );
    assert_eq!(
        answer.responses["areas"],
        AnswerValue::Selections(vec!["risk".into()])
    );
    assert_eq!(
        answer.responses["details"],
        AnswerValue::Text("  Привет\n\u{1b}[2J".into())
    );
    assert_eq!(answer.responses["confirmed"], AnswerValue::Boolean(false));
    assert_eq!(t.calls, 5);
}
#[test]
fn noninteractive_does_no_terminal_io_or_default_acceptance() {
    let p = fixture();
    let mut t = Terminal::new(vec![]);
    assert_eq!(
        collect(p.run_id, &p, false, limits(), &mut t),
        Ok(Collected::NeedsInput)
    );
    assert_eq!(t.calls, 0);
}
#[test]
fn wrong_run_has_no_io() {
    let p = fixture();
    let mut t = Terminal::new(vec![]);
    assert_eq!(
        collect(Uuid::nil(), &p, true, limits(), &mut t),
        Err(CollectError::WrongRun)
    );
    assert_eq!(t.calls, 0);
}
#[test]
fn defaults_require_explicit_choice_and_omissions_stay_absent() {
    let p = fixture();
    let mut t = Terminal::new(vec![
        FieldChoice::UseDefault,
        FieldChoice::Omit,
        FieldChoice::Omit,
        FieldChoice::UseDefault,
    ]);
    let Collected::Answer { answer, .. } = run(&p, &mut t).unwrap() else {
        panic!()
    };
    assert_eq!(answer.responses.len(), 2);
    assert_eq!(answer.responses["confirmed"], AnswerValue::Boolean(false));
}
#[test]
fn required_false_checkbox_is_valid() {
    let mut p = single(3);
    p.prompt.fields[0].required = true;
    assert!(matches!(
        run(&p, &mut Terminal::new(vec![FieldChoice::Checkbox(false)])),
        Ok(Collected::Answer { .. })
    ));
}
#[test]
fn required_text_rejects_python_whitespace_and_empty_default() {
    let mut p = single(2);
    p.prompt.fields[0].required = true;
    for s in ["", " \n\u{1c}\u{1f}"] {
        assert_eq!(
            run(&p, &mut Terminal::new(vec![FieldChoice::Text(s.into())])),
            Err(CollectError::InvalidAnswer)
        );
    }
    p.prompt.fields[0].default = Some(AnswerValue::Text("".into()));
    assert_eq!(
        run(&p, &mut Terminal::new(vec![FieldChoice::UseDefault])),
        Err(CollectError::InvalidAnswer)
    );
}
#[test]
fn required_omission_is_not_replaced_by_default() {
    let p = single(0);
    assert_eq!(
        run(&p, &mut Terminal::new(vec![FieldChoice::Omit])),
        Err(CollectError::InvalidAnswer)
    );
}
#[test]
fn selection_indices_are_validated_and_no_duplicates_allowed() {
    let p = single(1);
    for choice in [
        FieldChoice::MultiSelect(vec![1]),
        FieldChoice::MultiSelect(vec![0, 0]),
        FieldChoice::Checkbox(true),
    ] {
        assert_eq!(
            run(&p, &mut Terminal::new(vec![choice])),
            Err(CollectError::InvalidAnswer)
        );
    }
    let mut p = p;
    p.prompt.fields[0].required = true;
    assert_eq!(
        run(
            &p,
            &mut Terminal::new(vec![FieldChoice::MultiSelect(vec![])])
        ),
        Err(CollectError::InvalidAnswer)
    );
}
#[test]
fn skip_is_explicit_allowed_and_has_no_responses() {
    let mut p = fixture();
    let mut t = Terminal::new(vec![]);
    t.begin = Some(PromptChoice::Skip);
    assert_eq!(run(&p, &mut t), Err(CollectError::SkipForbidden));
    p.prompt.skip_allowed = true;
    let mut t = Terminal::new(vec![]);
    t.begin = Some(PromptChoice::Skip);
    let Collected::Answer { answer, .. } = run(&p, &mut t).unwrap() else {
        panic!()
    };
    assert!(answer.skipped);
    assert!(answer.responses.is_empty());
    assert_eq!(t.calls, 1);
}
#[test]
fn leaving_or_interruption_discards_partial_answers() {
    for (choice, expected) in [
        (FieldChoice::Leave, Collected::NeedsInput),
        (FieldChoice::Interrupt, Collected::Interrupted),
    ] {
        let p = fixture();
        assert_eq!(
            run(&p, &mut Terminal::new(vec![FieldChoice::Select(0), choice])),
            Ok(expected)
        );
    }
}
#[test]
fn malformed_schemas_fail_before_terminal_io() {
    for case in 0..5 {
        let mut p = fixture();
        match case {
            0 => p.prompt.fields.push(p.prompt.fields[0].clone()),
            1 => {
                let option = p.prompt.fields[0].options[0].clone();
                p.prompt.fields[0].options.push(option);
            }
            2 => p.prompt.fields[0].options.clear(),
            3 => p.prompt.fields[0].field_type = FieldType::Text,
            _ => p.prompt.fields[0].default = Some(AnswerValue::Boolean(true)),
        }
        let mut t = Terminal::new(vec![]);
        assert_eq!(run(&p, &mut t), Err(CollectError::InvalidSchema));
        assert_eq!(t.calls, 0);
    }
}
#[test]
fn explicit_caps_stop_oversized_schema_and_answer() {
    let p = fixture();
    let mut t = Terminal::new(vec![]);
    assert_eq!(
        collect(
            p.run_id,
            &p,
            true,
            Limits {
                fields: 0,
                ..limits()
            },
            &mut t
        ),
        Err(CollectError::LimitExceeded)
    );
    assert_eq!(t.calls, 0);
    assert_eq!(
        collect(
            p.run_id,
            &p,
            true,
            Limits {
                options_per_field: 0,
                ..limits()
            },
            &mut t
        ),
        Err(CollectError::LimitExceeded)
    );
    let mut p = single(2);
    p.prompt.fields[0].default = None;
    assert_eq!(
        collect(
            p.run_id,
            &p,
            true,
            Limits {
                text_chars: 1,
                ..limits()
            },
            &mut Terminal::new(vec![FieldChoice::Text("аб".into())])
        ),
        Err(CollectError::InvalidAnswer)
    );
}
#[test]
fn display_is_escaped_but_selection_retains_original_value() {
    let mut p = single(0);
    p.prompt.title = "title\u{1b}[2J".into();
    p.prompt.description = "desc\u{202e}".into();
    let f = &mut p.prompt.fields[0];
    f.label = "label\r".into();
    f.description = Some("description\u{1b}".into());
    f.options[0].value = "raw\u{1b}[2J".into();
    f.options[0].label = "option\u{202e}".into();
    f.options[0].description = Some("detail\u{1b}".into());
    f.default = Some(AnswerValue::Text(f.options[0].value.clone()));
    let mut t = Terminal::new(vec![FieldChoice::Select(0)]);
    let Collected::Answer { answer, .. } = run(&p, &mut t).unwrap() else {
        panic!()
    };
    assert_eq!(
        answer.responses["choice"],
        AnswerValue::Text("raw\u{1b}[2J".into())
    );
    let header = t.header.as_ref().unwrap();
    let view = &t.views[0];
    for text in [
        &header.title,
        &header.description,
        &view.label,
        view.description.as_ref().unwrap(),
        &view.options[0].0,
        &view.options[0].1,
        view.options[0].2.as_ref().unwrap(),
        view.default.as_ref().unwrap(),
    ] {
        assert!(!text.contains(['\u{1b}', '\r', '\u{202e}']));
    }
    assert!(t.views[0].options[0].1.contains("\\u{1b}"));
    assert!(!t.header.unwrap().description.contains('\u{202e}'));
}
#[test]
fn missing_terminal_input_is_error_not_answer() {
    assert_eq!(
        run(&fixture(), &mut Terminal::new(vec![])),
        Err(CollectError::Terminal)
    );
}
#[test]
fn explicit_selection_preserves_order_and_empty_option_value() {
    let mut p = single(1);
    p.prompt.fields[0]
        .options
        .push(council_cli::wire::PromptOption {
            value: "".into(),
            label: "empty".into(),
            description: None,
        });
    let Collected::Answer { answer, .. } = run(
        &p,
        &mut Terminal::new(vec![FieldChoice::MultiSelect(vec![1, 0])]),
    )
    .unwrap() else {
        panic!()
    };
    assert_eq!(
        answer.responses["areas"],
        AnswerValue::Selections(vec!["".into(), "risk".into()])
    );
}

#[test]
fn text_choice_cannot_bypass_selection_index_mapping() {
    assert_eq!(
        run(
            &single(0),
            &mut Terminal::new(vec![FieldChoice::Text("first".into())])
        ),
        Err(CollectError::InvalidAnswer)
    );
}
