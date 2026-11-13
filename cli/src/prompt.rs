//! Unwired prompt collection for future ask/resume drivers. No network or storage.
//! Backend schema/answer validation remains authoritative. Terminal implementations
//! receive display copies and return explicit choices; values sent back retain the
//! original bytes. No automatic retry, default acceptance or prompt-answer replay.
use crate::input::is_blank;
use crate::output::terminal_text;
use crate::wire::{AnswerRequest, AnswerValue, FieldType, PromptField, PromptInstance};
use std::collections::{BTreeMap, HashSet};
use uuid::Uuid;

#[derive(Clone, Copy)]
pub struct Limits {
    pub fields: usize,
    pub options_per_field: usize,
    pub text_chars: usize,
}
#[derive(Debug, PartialEq, Eq)]
pub enum CollectError {
    WrongRun,
    InvalidSchema,
    LimitExceeded,
    InvalidAnswer,
    SkipForbidden,
    Terminal,
}
#[derive(Debug, PartialEq, Eq)]
pub enum Collected {
    NeedsInput,
    Interrupted,
    Answer {
        run_id: Uuid,
        instance_id: Uuid,
        answer: AnswerRequest,
    },
}
#[derive(Debug)]
pub struct PromptView {
    pub title: String,
    pub description: String,
    pub skip_allowed: bool,
}
#[derive(Debug)]
pub struct FieldView {
    pub field_type: FieldType,
    pub label: String,
    pub description: Option<String>,
    pub required: bool,
    /// Display labels/values, never wire values. Indices refer to this order.
    pub options: Vec<(String, String, Option<String>)>,
    /// Display only. Accepting it requires FieldChoice::UseDefault.
    pub default: Option<String>,
}
#[derive(Debug)]
pub enum PromptChoice {
    Answer,
    Skip,
    Leave,
    Interrupt,
}
#[derive(Debug)]
pub enum FieldChoice {
    Text(String),
    Select(usize),
    MultiSelect(Vec<usize>),
    Checkbox(bool),
    UseDefault,
    Omit,
    Leave,
    Interrupt,
}
/// Implementations must bound reads using max_text_chars and provide a controlling
/// terminal when stdin carries the question. No real terminal implementation here.
/// Return an error on EOF/output failure, never an implicit default or answer.
pub trait PromptTerminal {
    fn begin(&mut self, prompt: &PromptView) -> Result<PromptChoice, CollectError>;
    fn field(
        &mut self,
        field: &FieldView,
        max_text_chars: usize,
    ) -> Result<FieldChoice, CollectError>;
}

fn valid_value(field: &PromptField, value: &AnswerValue, required: bool, limits: Limits) -> bool {
    match (&field.field_type, value) {
        (FieldType::Text, AnswerValue::Text(s)) => {
            s.chars().count() <= limits.text_chars && (!required || !is_blank(s))
        }
        (FieldType::Checkbox, AnswerValue::Boolean(_)) => true,
        (FieldType::Select, AnswerValue::Text(s)) => field.options.iter().any(|o| o.value == *s),
        (FieldType::MultiSelect, AnswerValue::Selections(values)) => {
            (!required || !values.is_empty())
                && values.len() <= field.options.len()
                && values.iter().collect::<HashSet<_>>().len() == values.len()
                && values
                    .iter()
                    .all(|v| field.options.iter().any(|o| o.value == *v))
        }
        _ => false,
    }
}
fn validate(instance: &PromptInstance, limits: Limits) -> Result<(), CollectError> {
    let fields = &instance.prompt.fields;
    if fields.len() > limits.fields {
        return Err(CollectError::LimitExceeded);
    }
    let mut ids = HashSet::new();
    for field in fields {
        if !ids.insert(&field.id) {
            return Err(CollectError::InvalidSchema);
        }
        if field.options.len() > limits.options_per_field {
            return Err(CollectError::LimitExceeded);
        }
        let values: HashSet<_> = field.options.iter().map(|o| &o.value).collect();
        let selection = matches!(field.field_type, FieldType::Select | FieldType::MultiSelect);
        if values.len() != field.options.len() || selection == values.is_empty() {
            return Err(CollectError::InvalidSchema);
        }
        if field
            .default
            .as_ref()
            .is_some_and(|v| !valid_value(field, v, false, limits))
        {
            return Err(CollectError::InvalidSchema);
        }
    }
    Ok(())
}
fn view(field: &PromptField) -> FieldView {
    FieldView {
        field_type: field.field_type.clone(),
        label: terminal_text(&field.label),
        description: field.description.as_deref().map(terminal_text),
        required: field.required,
        options: field
            .options
            .iter()
            .map(|o| {
                (
                    terminal_text(&o.label),
                    terminal_text(&o.value),
                    o.description.as_deref().map(terminal_text),
                )
            })
            .collect(),
        default: field
            .default
            .as_ref()
            .map(|v| terminal_text(&serde_json::to_string(v).expect("AnswerValue serializes"))),
    }
}
/// One bounded pass: at most one begin call and one call per field. Invalid input
/// returns an error rather than looping. The caller must explicitly decide whether
/// to offer another collection attempt. Partial answers are discarded on detach.
pub fn collect(
    expected_run: Uuid,
    instance: &PromptInstance,
    can_prompt: bool,
    limits: Limits,
    terminal: &mut impl PromptTerminal,
) -> Result<Collected, CollectError> {
    if instance.run_id != expected_run {
        return Err(CollectError::WrongRun);
    }
    if !can_prompt {
        return Ok(Collected::NeedsInput);
    }
    validate(instance, limits)?;
    let prompt = &instance.prompt;
    match terminal.begin(&PromptView {
        title: terminal_text(&prompt.title),
        description: terminal_text(&prompt.description),
        skip_allowed: prompt.skip_allowed,
    })? {
        PromptChoice::Skip => {
            if !prompt.skip_allowed {
                return Err(CollectError::SkipForbidden);
            }
            return Ok(Collected::Answer {
                run_id: expected_run,
                instance_id: instance.instance_id,
                answer: AnswerRequest {
                    responses: BTreeMap::new(),
                    skipped: true,
                },
            });
        }
        PromptChoice::Leave => return Ok(Collected::NeedsInput),
        PromptChoice::Interrupt => return Ok(Collected::Interrupted),
        PromptChoice::Answer => {}
    }
    let mut responses = BTreeMap::new();
    for field in &prompt.fields {
        let value = match terminal.field(&view(field), limits.text_chars)? {
            FieldChoice::Leave => return Ok(Collected::NeedsInput),
            FieldChoice::Interrupt => return Ok(Collected::Interrupted),
            FieldChoice::Omit => {
                if field.required {
                    return Err(CollectError::InvalidAnswer);
                }
                continue;
            }
            FieldChoice::UseDefault => field.default.clone().ok_or(CollectError::InvalidAnswer)?,
            FieldChoice::Text(s) => {
                if field.field_type != FieldType::Text {
                    return Err(CollectError::InvalidAnswer);
                }
                AnswerValue::Text(s)
            }
            FieldChoice::Checkbox(b) => AnswerValue::Boolean(b),
            FieldChoice::Select(i) => {
                if field.field_type != FieldType::Select {
                    return Err(CollectError::InvalidAnswer);
                }
                AnswerValue::Text(
                    field
                        .options
                        .get(i)
                        .ok_or(CollectError::InvalidAnswer)?
                        .value
                        .clone(),
                )
            }
            FieldChoice::MultiSelect(indices) => {
                if field.field_type != FieldType::MultiSelect || indices.len() > field.options.len()
                {
                    return Err(CollectError::InvalidAnswer);
                }
                AnswerValue::Selections(
                    indices
                        .into_iter()
                        .map(|i| {
                            field
                                .options
                                .get(i)
                                .map(|o| o.value.clone())
                                .ok_or(CollectError::InvalidAnswer)
                        })
                        .collect::<Result<_, _>>()?,
                )
            }
        };
        if !valid_value(field, &value, field.required, limits) {
            return Err(CollectError::InvalidAnswer);
        }
        responses.insert(field.id.as_str().to_owned(), value);
    }
    Ok(Collected::Answer {
        run_id: expected_run,
        instance_id: instance.instance_id,
        answer: AnswerRequest {
            responses,
            skipped: false,
        },
    })
}
