//! Draft v1 wire contracts. These types do not enable the unavailable run API.
//!
//! Deserialization enforces the backend's structural and cross-field invariants.
//! Request size limits are supplied by configuration through `validate_limits`.
//! Prompt answer/schema validation and provider execution remain backend concerns.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, HashSet};
use uuid::Uuid;

#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct Identifier(String);

impl Identifier {
    pub fn new(value: String) -> Result<Self, String> {
        if value.is_empty()
            || value.len() > 128
            || !value
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
        {
            return Err(
                "Identifier must contain 1–128 ASCII letters, digits, underscores or hyphens"
                    .into(),
            );
        }
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl TryFrom<String> for Identifier {
    type Error = String;
    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl From<Identifier> for String {
    fn from(value: Identifier) -> Self {
        value.0
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunLifecycle {
    Queued,
    Running,
    WaitingInput,
    NeedsRetry,
    Complete,
    Failed,
    Cancelling,
    Cancelled,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InteractionPolicy {
    Persistent,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RecoveryPolicy {
    ExplicitRetry,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "RawCreateRunRequest")]
pub struct CreateRunRequest {
    pub content: String,
    pub negative_prompt: Option<String>,
    pub skill_ids: Vec<Identifier>,
    pub refine: bool,
    pub interaction_policy: InteractionPolicy,
    pub recovery_policy: RecoveryPolicy,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawCreateRunRequest {
    content: String,
    #[serde(default)]
    negative_prompt: Option<String>,
    #[serde(default)]
    skill_ids: Vec<Identifier>,
    #[serde(default)]
    refine: bool,
    interaction_policy: InteractionPolicy,
    recovery_policy: RecoveryPolicy,
}

impl TryFrom<RawCreateRunRequest> for CreateRunRequest {
    type Error = String;
    fn try_from(raw: RawCreateRunRequest) -> Result<Self, Self::Error> {
        let request = Self {
            content: raw.content,
            negative_prompt: raw.negative_prompt,
            skill_ids: raw.skill_ids,
            refine: raw.refine,
            interaction_policy: raw.interaction_policy,
            recovery_policy: raw.recovery_policy,
        };
        request.validate_limits(usize::MAX, usize::MAX, usize::MAX)?;
        Ok(request)
    }
}

impl CreateRunRequest {
    /// Match Python's Unicode-code-point limits, not UTF-8 bytes or graphemes.
    /// Call again before submission even for a request constructed in Rust.
    pub fn validate_limits(
        &self,
        max_prompt_chars: usize,
        max_negative_chars: usize,
        max_skills: usize,
    ) -> Result<(), String> {
        if self
            .content
            .trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
            .is_empty()
        {
            return Err("Prompt cannot be empty".into());
        }
        if self.content.chars().count() > max_prompt_chars {
            return Err("Prompt exceeds the configured character limit".into());
        }
        if self
            .negative_prompt
            .as_ref()
            .is_some_and(|text| text.chars().count() > max_negative_chars)
        {
            return Err("Negative prompt exceeds the configured character limit".into());
        }
        if self.skill_ids.len() > max_skills {
            return Err("Too many selected skills".into());
        }
        let unique: HashSet<_> = self.skill_ids.iter().collect();
        if unique.len() != self.skill_ids.len() {
            return Err("Duplicate skill IDs are not allowed".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum AnswerValue {
    Text(String),
    Boolean(bool),
    Selections(Vec<String>),
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PromptOption {
    pub value: String,
    pub label: String,
    #[serde(default)]
    pub description: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FieldType {
    Select,
    MultiSelect,
    Text,
    Checkbox,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PromptField {
    pub id: Identifier,
    #[serde(rename = "type")]
    pub field_type: FieldType,
    pub label: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub options: Vec<PromptOption>,
    #[serde(default)]
    pub default: Option<AnswerValue>,
    #[serde(default)]
    pub required: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PromptType {
    PreQuery,
    PromptRefinement,
    ResponseReview,
    SynthesisPrefs,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PromptKind {
    #[default]
    InteractivePrompt,
}

fn default_true() -> bool {
    true
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PromptSchema {
    #[serde(rename = "type", default)]
    pub kind: PromptKind,
    pub prompt_id: Identifier,
    pub prompt_type: PromptType,
    pub title: String,
    pub description: String,
    pub fields: Vec<PromptField>,
    #[serde(default = "default_true")]
    pub skip_allowed: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PromptInstance {
    pub run_id: Uuid,
    pub instance_id: Uuid,
    pub prompt: PromptSchema,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "RawAnswerRequest")]
pub struct AnswerRequest {
    pub responses: BTreeMap<String, AnswerValue>,
    pub skipped: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawAnswerRequest {
    #[serde(default)]
    responses: BTreeMap<String, AnswerValue>,
    #[serde(default)]
    skipped: bool,
}

impl TryFrom<RawAnswerRequest> for AnswerRequest {
    type Error = String;
    fn try_from(raw: RawAnswerRequest) -> Result<Self, Self::Error> {
        if raw.skipped && !raw.responses.is_empty() {
            return Err("A skipped prompt cannot also contain answers".into());
        }
        Ok(Self {
            responses: raw.responses,
            skipped: raw.skipped,
        })
    }
}

fn nonempty<'de, D: serde::Deserializer<'de>>(deserializer: D) -> Result<String, D::Error> {
    let text = String::deserialize(deserializer)?;
    if text.is_empty() {
        return Err(serde::de::Error::custom("Value cannot be empty"));
    }
    Ok(text)
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetryRequest {
    #[serde(deserialize_with = "nonempty")]
    pub revision: String,
}

/// Legacy records retain the export reference and never invent a run ID.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResultReference {
    pub conversation_id: Identifier,
    pub assistant_index: u64,
    #[serde(default)]
    pub run_id: Option<Uuid>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunReceipt {
    pub run_id: Uuid,
    pub conversation_id: Identifier,
    #[serde(default)]
    pub assistant_index: Option<u64>,
    pub lifecycle: RunLifecycle,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetryNotice {
    #[serde(deserialize_with = "nonempty")]
    pub revision: String,
    #[serde(deserialize_with = "nonempty")]
    pub operation: String,
    #[serde(deserialize_with = "nonempty")]
    pub explanation: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "RawRunSnapshot")]
pub struct RunSnapshot {
    pub run_id: Uuid,
    pub conversation_id: Identifier,
    pub assistant_index: Option<u64>,
    pub lifecycle: RunLifecycle,
    pub cursor: String,
    pub stage: Option<String>,
    pub pending_prompt: Option<PromptInstance>,
    pub retry: Option<RetryNotice>,
    pub result: Option<ResultReference>,
    pub stages: BTreeMap<String, Value>,
    pub provenance: BTreeMap<String, Value>,
    pub error: Option<String>,
}

// Deliberately not `flatten`: serde cannot combine flatten and deny_unknown_fields.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawRunSnapshot {
    run_id: Uuid,
    conversation_id: Identifier,
    #[serde(default)]
    assistant_index: Option<u64>,
    lifecycle: RunLifecycle,
    cursor: String,
    #[serde(default)]
    stage: Option<String>,
    #[serde(default)]
    pending_prompt: Option<PromptInstance>,
    #[serde(default)]
    retry: Option<RetryNotice>,
    #[serde(default)]
    result: Option<ResultReference>,
    #[serde(default)]
    stages: BTreeMap<String, Value>,
    #[serde(default)]
    provenance: BTreeMap<String, Value>,
    #[serde(default)]
    error: Option<String>,
}

impl TryFrom<RawRunSnapshot> for RunSnapshot {
    type Error = String;
    fn try_from(raw: RawRunSnapshot) -> Result<Self, Self::Error> {
        if (raw.lifecycle == RunLifecycle::WaitingInput) != raw.pending_prompt.is_some() {
            return Err("Only waiting_input has a pending prompt".into());
        }
        if raw
            .pending_prompt
            .as_ref()
            .is_some_and(|prompt| prompt.run_id != raw.run_id)
        {
            return Err("Pending prompt belongs to another run".into());
        }
        if (raw.lifecycle == RunLifecycle::NeedsRetry) != raw.retry.is_some() {
            return Err("Only needs_retry has a retry notice".into());
        }
        if raw.lifecycle == RunLifecycle::Complete && raw.result.is_none() {
            return Err("A completed run requires its saved result reference".into());
        }
        if raw.result.as_ref().is_some_and(|result| {
            result.run_id != Some(raw.run_id)
                || result.conversation_id != raw.conversation_id
                || Some(result.assistant_index) != raw.assistant_index
        }) {
            return Err("Saved result reference does not match this run".into());
        }
        Ok(Self {
            run_id: raw.run_id,
            conversation_id: raw.conversation_id,
            assistant_index: raw.assistant_index,
            lifecycle: raw.lifecycle,
            cursor: raw.cursor,
            stage: raw.stage,
            pending_prompt: raw.pending_prompt,
            retry: raw.retry,
            result: raw.result,
            stages: raw.stages,
            provenance: raw.provenance,
            error: raw.error,
        })
    }
}
