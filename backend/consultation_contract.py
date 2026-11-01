"""Draft CLI wire contract, not yet an implemented run API.

Version 0 advertised by /api/config means the durable run API is unavailable.
These types and fixtures specify the target v1 boundary without changing legacy
web submission, process-local prompts, or recovery behavior.
"""

from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    model_validator,
)

from .config import MAX_NEGATIVE_PROMPT_CHARS, MAX_PROMPT_CHARS, SKILLS_MAX_SELECTED
from .interactive import FieldType, PromptType

CLI_API_VERSION = 0
TARGET_CLI_API_VERSION = 1

Identifier = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Index = Annotated[int, Field(strict=True, ge=0)]
AnswerValue = Union[StrictStr, StrictBool, list[StrictStr]]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunLifecycle(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    NEEDS_RETRY = "needs_retry"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class CreateRunRequest(WireModel):
    content: Annotated[
        str, Field(strict=True, min_length=1, max_length=MAX_PROMPT_CHARS)
    ]
    negative_prompt: Optional[
        Annotated[str, Field(strict=True, max_length=MAX_NEGATIVE_PROMPT_CHARS)]
    ] = None
    skill_ids: Annotated[list[Identifier], Field(max_length=SKILLS_MAX_SELECTED)] = (
        Field(default_factory=list)
    )
    refine: StrictBool = False
    # Required opt-in; never silently degrade to timeout/automatic retry behavior.
    interaction_policy: Literal["persistent"]
    recovery_policy: Literal["explicit_retry"]

    @model_validator(mode="after")
    def validate_question(self) -> "CreateRunRequest":
        if not self.content.strip():
            raise ValueError("Prompt cannot be empty")
        if len(set(self.skill_ids)) != len(self.skill_ids):
            raise ValueError("Duplicate skill IDs are not allowed")
        return self


class PromptOption(WireModel):
    value: StrictStr
    label: StrictStr
    description: Optional[StrictStr] = None


class PromptField(WireModel):
    id: Identifier
    type: FieldType
    label: StrictStr
    description: Optional[StrictStr] = None
    options: list[PromptOption] = Field(default_factory=list)
    default: Optional[AnswerValue] = None
    required: StrictBool = False


class PromptSchema(WireModel):
    type: Literal["interactive_prompt"] = "interactive_prompt"
    prompt_id: Identifier
    prompt_type: PromptType
    title: StrictStr
    description: StrictStr
    fields: list[PromptField]
    skip_allowed: StrictBool = True


class PromptInstance(WireModel):
    run_id: UUID
    instance_id: UUID
    prompt: PromptSchema


class AnswerRequest(WireModel):
    responses: dict[str, AnswerValue] = Field(default_factory=dict)
    skipped: StrictBool = False

    @model_validator(mode="after")
    def validate_skip(self) -> "AnswerRequest":
        if self.skipped and self.responses:
            raise ValueError("A skipped prompt cannot also contain answers")
        return self


class RetryRequest(WireModel):
    revision: Annotated[str, Field(strict=True, min_length=1)]


class ResultReference(WireModel):
    """Legacy results have a conversation/message reference, not a fabricated run."""

    conversation_id: Identifier
    assistant_index: Index
    run_id: Optional[UUID] = None


class RunReceipt(WireModel):
    run_id: UUID
    conversation_id: Identifier
    assistant_index: Optional[Index] = None
    lifecycle: RunLifecycle


class RetryNotice(WireModel):
    revision: Annotated[str, Field(strict=True, min_length=1)]
    operation: Annotated[str, Field(strict=True, min_length=1)]
    explanation: Annotated[str, Field(strict=True, min_length=1)]


class RunSnapshot(RunReceipt):
    """Full snapshot; stage payloads retain backend evidence without truncation."""

    cursor: StrictStr
    stage: Optional[StrictStr] = None
    pending_prompt: Optional[PromptInstance] = None
    retry: Optional[RetryNotice] = None
    result: Optional[ResultReference] = None
    # Existing stage payloads differ by stage/provider; preserve their JSON data.
    stages: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    error: Optional[StrictStr] = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "RunSnapshot":
        if (self.lifecycle == RunLifecycle.WAITING_INPUT) != (
            self.pending_prompt is not None
        ):
            raise ValueError("Only waiting_input has a pending prompt")
        if self.pending_prompt and self.pending_prompt.run_id != self.run_id:
            raise ValueError("Pending prompt belongs to another run")
        if (self.lifecycle == RunLifecycle.NEEDS_RETRY) != (self.retry is not None):
            raise ValueError("Only needs_retry has a retry notice")
        if self.lifecycle == RunLifecycle.COMPLETE and self.result is None:
            raise ValueError("A completed run requires its saved result reference")
        if self.result and (
            self.result.run_id != self.run_id
            or self.result.conversation_id != self.conversation_id
            or self.result.assistant_index != self.assistant_index
        ):
            raise ValueError("Saved result reference does not match this run")
        return self
