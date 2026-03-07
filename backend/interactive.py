"""Interactive prompts for user input during council processing."""

from enum import Enum
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict


class PromptType(str, Enum):
    """Types of interactive prompts."""
    PRE_QUERY = "pre_query"
    PROMPT_REFINEMENT = "prompt_refinement"
    RESPONSE_REVIEW = "response_review"
    SYNTHESIS_PREFS = "synthesis_prefs"


class FieldType(str, Enum):
    """Types of input fields."""
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    TEXT = "text"
    CHECKBOX = "checkbox"


@dataclass
class FieldOption:
    """An option for select/multi-select fields."""
    value: str
    label: str
    description: Optional[str] = None


@dataclass
class PromptField:
    """A single input field in an interactive prompt."""
    id: str
    type: FieldType
    label: str
    description: Optional[str] = None
    options: List[FieldOption] = field(default_factory=list)
    default: Optional[Any] = None
    required: bool = False


@dataclass
class InteractivePrompt:
    """An interactive prompt requesting user input."""
    prompt_id: str
    prompt_type: PromptType
    title: str
    description: str
    fields: List[PromptField]
    skip_allowed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "type": "interactive_prompt",
            "prompt_id": self.prompt_id,
            "prompt_type": self.prompt_type.value,
            "title": self.title,
            "description": self.description,
            "skip_allowed": self.skip_allowed,
            "fields": [],
        }
        for f in self.fields:
            field_dict = {
                "id": f.id,
                "type": f.type.value,
                "label": f.label,
                "required": f.required,
            }
            if f.description:
                field_dict["description"] = f.description
            if f.default is not None:
                field_dict["default"] = f.default
            if f.options:
                field_dict["options"] = [
                    {"value": o.value, "label": o.label, "description": o.description}
                    for o in f.options
                ]
            result["fields"].append(field_dict)
        return result


@dataclass
class PromptResponse:
    """User response to an interactive prompt."""
    prompt_id: str
    responses: Dict[str, Any]
    skipped: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        return {
            "prompt_id": self.prompt_id,
            "responses": self.responses,
            "skipped": self.skipped,
        }


def deserialize_prompt_response(payload: Optional[Dict[str, Any]]) -> Optional[PromptResponse]:
    """Restore a prompt response from persisted JSON data."""
    if not payload:
        return None
    prompt_id = payload.get("prompt_id")
    if not prompt_id:
        return None
    responses = payload.get("responses")
    return PromptResponse(
        prompt_id=str(prompt_id),
        responses=responses if isinstance(responses, dict) else {},
        skipped=bool(payload.get("skipped", False)),
    )


def serialize_prompt_responses(
    responses: Dict[str, PromptResponse],
) -> Dict[str, Dict[str, Any]]:
    """Convert collected prompt responses into JSON-serializable form."""
    return {
        key: response.to_dict()
        for key, response in responses.items()
    }


def deserialize_prompt_responses(
    payload: Optional[Dict[str, Any]],
) -> Dict[str, PromptResponse]:
    """Restore collected prompt responses from persisted JSON data."""
    if not isinstance(payload, dict):
        return {}

    restored: Dict[str, PromptResponse] = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        response = deserialize_prompt_response(value)
        if response is not None:
            restored[key] = response
    return restored


# Pre-defined prompts

def get_pre_query_prompt() -> InteractivePrompt:
    """Get the pre-query configuration prompt."""
    return InteractivePrompt(
        prompt_id="pre_query_config",
        prompt_type=PromptType.PRE_QUERY,
        title="Configure Response Style",
        description="Help the council understand your needs for better responses.",
        fields=[
            PromptField(
                id="domain",
                type=FieldType.SELECT,
                label="Domain",
                description="What area is your question about?",
                options=[
                    FieldOption("general", "General", "General knowledge questions"),
                    FieldOption("coding", "Coding/Programming", "Software development, algorithms, debugging"),
                    FieldOption("legal", "Legal", "Law, regulations, compliance"),
                    FieldOption("medical", "Medical/Health", "Health, medicine, wellness"),
                    FieldOption("creative", "Creative", "Writing, art, design, brainstorming"),
                    FieldOption("technical", "Technical/Engineering", "Engineering, science, technical analysis"),
                    FieldOption("business", "Business", "Strategy, finance, management"),
                ],
                default="general",
            ),
            PromptField(
                id="style",
                type=FieldType.SELECT,
                label="Response Style",
                description="How detailed should responses be?",
                options=[
                    FieldOption("concise", "Concise", "Brief, to-the-point answers"),
                    FieldOption("detailed", "Detailed", "Comprehensive with explanations"),
                    FieldOption("comprehensive", "Comprehensive", "In-depth analysis with examples"),
                ],
                default="detailed",
            ),
            PromptField(
                id="priority",
                type=FieldType.SELECT,
                label="Priority Criteria",
                description="What matters most in the response?",
                options=[
                    FieldOption("accuracy", "Accuracy", "Factual correctness above all"),
                    FieldOption("clarity", "Clarity", "Easy to understand explanations"),
                    FieldOption("completeness", "Completeness", "Cover all aspects thoroughly"),
                    FieldOption("actionability", "Actionability", "Practical, implementable advice"),
                ],
                default="accuracy",
            ),
        ],
        skip_allowed=True,
    )


def get_prompt_refinement_prompt(
    suggestion: Dict[str, Any],
    iteration: int = 1,
) -> InteractivePrompt:
    """Get the prompt refinement review prompt."""
    intent = (suggestion.get("intent") or "").strip()
    clarifying_questions = suggestion.get("clarifying_questions") or []
    refined_prompt = (suggestion.get("refined_prompt") or "").strip()

    description = "Review the refined prompt and choose how to proceed. Skipping keeps your original prompt."
    if intent:
        description = f"Inferred intent: {intent}\n\n{description}"
    if iteration > 1:
        description = f"{description}\n\nFollow-up questions may appear below."

    fields = [
        PromptField(
            id="refinement_action",
            type=FieldType.SELECT,
            label="Next step",
            description="Choose whether to use the refined prompt, keep the original, or refine again.",
            options=[
                FieldOption("use_refined", "Use refined prompt", "Proceed with the refined prompt below"),
                FieldOption("use_original", "Use original prompt", "Proceed with your original prompt"),
                FieldOption("refine_again", "Refine with more details", "Provide more context and re-generate"),
            ],
            default="use_refined",
            required=True,
        ),
        PromptField(
            id="refined_prompt",
            type=FieldType.TEXT,
            label="Refined prompt",
            description="Edit if needed. Used when you choose 'Use refined prompt'.",
            default=refined_prompt,
        ),
    ]

    for idx, question in enumerate(clarifying_questions):
        label = str(question).strip()
        if not label:
            continue
        fields.append(
            PromptField(
                id=f"clarifying_answer_{idx}",
                type=FieldType.TEXT,
                label=label,
                description=(
                    "Optional. Answer to improve the prompt, then choose "
                    "'Refine with more details'."
                ),
                default="",
            )
        )

    fields.append(
        PromptField(
            id="additional_insights",
            type=FieldType.TEXT,
            label="Additional insights (optional)",
            description="Extra context to improve the refined prompt.",
            default="",
        )
    )

    title_suffix = f" (Round {iteration})" if iteration > 1 else ""
    return InteractivePrompt(
        prompt_id="prompt_refinement",
        prompt_type=PromptType.PROMPT_REFINEMENT,
        title=f"Refine Your Prompt{title_suffix}",
        description=description,
        fields=fields,
        skip_allowed=True,
    )


def get_response_review_prompt(responses: List[Dict[str, Any]], label_to_model: Dict[str, str]) -> InteractivePrompt:
    """Get the response review prompt with actual response labels."""
    # Build options for excluding responses
    exclude_options = [
        FieldOption(
            value=resp.get("model", f"model_{i}"),
            label=f"Response {chr(65 + i)} ({resp.get('model', '').split('/')[-1]})",
            description=f"Exclude this response from further consideration"
        )
        for i, resp in enumerate(responses)
    ]

    return InteractivePrompt(
        prompt_id="response_review",
        prompt_type=PromptType.RESPONSE_REVIEW,
        title="Review Initial Responses",
        description="Optionally exclude weak responses or specify concerns for gap analysis.",
        fields=[
            PromptField(
                id="exclude_responses",
                type=FieldType.MULTI_SELECT,
                label="Exclude Responses (optional)",
                description="Select any responses you want to exclude from consideration.",
                options=exclude_options,
                default=[],
            ),
            PromptField(
                id="gap_concerns",
                type=FieldType.TEXT,
                label="Specific Concerns (optional)",
                description="What gaps, risks, or edge cases should models check for?",
                default="",
            ),
        ],
        skip_allowed=True,
    )


def get_synthesis_prefs_prompt() -> InteractivePrompt:
    """Get the synthesis preferences prompt."""
    return InteractivePrompt(
        prompt_id="synthesis_prefs",
        prompt_type=PromptType.SYNTHESIS_PREFS,
        title="Synthesis Preferences",
        description="How should the chairman synthesize the final response?",
        fields=[
            PromptField(
                id="synthesis_mode",
                type=FieldType.SELECT,
                label="Synthesis Mode",
                description="How should responses be combined?",
                options=[
                    FieldOption("consensus", "Consensus", "Find common ground across responses"),
                    FieldOption("best_of_breed", "Best of Breed", "Pick best elements from each response"),
                    FieldOption("comprehensive", "Comprehensive", "Include all perspectives and trade-offs"),
                    FieldOption("weighted", "Weighted by Rankings", "Favor higher-ranked responses"),
                ],
                default="best_of_breed",
            ),
            PromptField(
                id="focus_areas",
                type=FieldType.TEXT,
                label="Focus Areas (optional)",
                description="Any specific aspects you want emphasized in the final answer?",
                default="",
            ),
        ],
        skip_allowed=True,
    )


# Interactive configuration data class

@dataclass
class InteractiveConfig:
    """Configuration collected from interactive prompts."""
    domain: str = "general"
    style: str = "detailed"
    priority: str = "accuracy"
    excluded_models: List[str] = field(default_factory=list)
    gap_concerns: Optional[str] = None
    synthesis_mode: str = "best_of_breed"
    focus_areas: Optional[str] = None

    @classmethod
    def from_responses(cls, responses: Dict[str, PromptResponse]) -> "InteractiveConfig":
        """Build config from collected prompt responses."""
        config = cls()

        if "pre_query_config" in responses and not responses["pre_query_config"].skipped:
            r = responses["pre_query_config"].responses
            config.domain = r.get("domain", "general")
            config.style = r.get("style", "detailed")
            config.priority = r.get("priority", "accuracy")

        if "response_review" in responses and not responses["response_review"].skipped:
            r = responses["response_review"].responses
            config.excluded_models = r.get("exclude_responses", [])
            config.gap_concerns = r.get("gap_concerns") or None

        if "synthesis_prefs" in responses and not responses["synthesis_prefs"].skipped:
            r = responses["synthesis_prefs"].responses
            config.synthesis_mode = r.get("synthesis_mode", "best_of_breed")
            config.focus_areas = r.get("focus_areas") or None

        return config

    def get_system_prompt_modifier(self) -> str:
        """Generate system prompt additions based on config."""
        parts = []

        domain_hints = {
            "coding": "Provide code examples where helpful. Be precise about syntax and best practices.",
            "legal": "Be careful about jurisdiction-specific advice. Note when professional consultation is needed.",
            "medical": "Emphasize when professional medical advice is necessary. Be cautious with diagnoses.",
            "creative": "Be imaginative and explore multiple creative directions.",
            "technical": "Include technical details and explain underlying principles.",
            "business": "Consider practical business implications and trade-offs.",
        }
        if self.domain in domain_hints:
            parts.append(domain_hints[self.domain])

        style_hints = {
            "concise": "Keep responses brief and focused. Avoid unnecessary elaboration.",
            "comprehensive": "Provide thorough analysis with examples and edge cases.",
        }
        if self.style in style_hints:
            parts.append(style_hints[self.style])

        priority_hints = {
            "accuracy": "Prioritize factual accuracy. Acknowledge uncertainty when present.",
            "clarity": "Prioritize clear explanations that are easy to understand.",
            "completeness": "Cover all relevant aspects of the question.",
            "actionability": "Focus on practical, implementable advice.",
        }
        if self.priority in priority_hints:
            parts.append(priority_hints[self.priority])

        return " ".join(parts) if parts else ""
