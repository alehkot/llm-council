"""Validate complete saved critiques, without ranking or advancing a run."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StrictFloat, StrictInt, StrictStr

from . import run_negative_results, run_stage1_inputs
from .consultation_contract import WireModel

InvalidReason = Literal[
    "json", "duplicate_key", "fields", "peer_scope", "score", "summary"
]
Score = Annotated[StrictFloat, Field(ge=0, le=10, allow_inf_nan=False)]
_FIELDS = {"self_score", "self_summary", "peer_scores", "peer_summaries"}
_OPEN_FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n", re.IGNORECASE)
_CLOSE_FENCE = re.compile(r"^```[ \t]*\r?$", re.MULTILINE)


class InvalidCritique(ValueError):
    """Malformed completed evidence, distinct from work that is not complete."""

    def __init__(self, reason: InvalidReason, reviewer: str | None = None) -> None:
        self.reason = reason
        self.reviewer = reviewer
        where = f" for {reviewer}" if reviewer else ""
        super().__init__(f"Invalid saved critique{where}: {reason}")


class ScoredAnswer(WireModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    label: StrictStr
    score: Score
    summary: StrictStr = Field(repr=False)


class ValidatedCritique(WireModel):
    """One validated reviewer and its exact saved attempt identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    model: StrictStr
    request_sha256: run_stage1_inputs.Digest
    attempt: UUID
    retry_of: UUID | None
    own_answer: ScoredAnswer
    peers: tuple[ScoredAnswer, ...]


class ValidatedResults(WireModel):
    """Detached, all-reviewer internal evidence, not a ranking or an HTTP response."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[1] = 1
    source_revision: Annotated[StrictInt, Field(ge=1)]
    inputs_sha256: run_stage1_inputs.Digest
    stage1_source_sha256: run_stage1_inputs.Digest
    review_policy_sha256: run_stage1_inputs.Digest
    negative_prompt_source: Literal["request", "review"]
    provider_key: StrictStr
    label_to_model: dict[StrictStr, StrictStr]
    critiques: tuple[ValidatedCritique, ...] = Field(repr=False)


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise InvalidCritique("duplicate_key")
        output[key] = value
    return output


def _nonstandard_constant(_: str) -> None:
    # Python's decoder otherwise accepts NaN and Infinity outside the JSON grammar.
    raise InvalidCritique("json")


def _object(text: str) -> dict[str, Any]:
    payload = text.strip()
    # The saved prompt requests one JSON fence and permits prose afterwards.
    # A bare object is also accepted, but neither path searches prose for JSON.
    if payload.startswith("```"):
        opening = _OPEN_FENCE.match(payload)
        if opening is None:
            raise InvalidCritique("json")
        closing = _CLOSE_FENCE.search(payload, opening.end())
        if closing is None:
            raise InvalidCritique("json")
        payload = payload[opening.end() : closing.start()]
    try:
        decoded = json.loads(
            payload,
            object_pairs_hook=_unique_keys,
            parse_constant=_nonstandard_constant,
            parse_float=Decimal,
        )
    except InvalidCritique:
        raise
    except (ValueError, RecursionError, InvalidOperation):
        raise InvalidCritique("json") from None
    if not isinstance(decoded, dict) or set(decoded) != _FIELDS:
        raise InvalidCritique("fields")
    return decoded


def _answer(label: str, score: Any, summary: Any) -> ScoredAnswer:
    # Check the exact JSON number first: binary floats can round values just
    # outside the interval to 0 or 10. Booleans and numeric strings are not numbers.
    if (
        type(score) not in (int, Decimal)
        or (isinstance(score, Decimal) and not score.is_finite())
        or not 0 <= score <= 10
    ):
        raise InvalidCritique("score")
    if not isinstance(summary, str) or not summary.strip():
        raise InvalidCritique("summary")
    try:
        summary.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidCritique("summary") from None
    return ScoredAnswer(label=label, score=float(score), summary=summary)


def _parse(result: run_negative_results.CritiqueResult) -> ValidatedCritique:
    data = _object(result.response.content)
    scores, summaries = data["peer_scores"], data["peer_summaries"]
    expected = set(result.peer_labels)
    if (
        not isinstance(scores, dict)
        or not isinstance(summaries, dict)
        or set(scores) != expected
        or set(summaries) != expected
    ):
        raise InvalidCritique("peer_scope")
    return ValidatedCritique(
        model=result.model,
        request_sha256=result.request_sha256,
        attempt=result.attempt,
        retry_of=result.retry_of,
        own_answer=_answer(result.self_label, data["self_score"], data["self_summary"]),
        peers=tuple(
            _answer(label, scores[label], summaries[label])
            for label in result.peer_labels
        ),
    )


def load_validated_results(run_id: str) -> ValidatedResults | None:
    """Validate complete evidence or fail as a unit; never emit partial scores.

    None denotes incomplete transport evidence. InvalidCritique is only for a
    completed roster with a malformed review. It exposes a fixed reason and the
    saved reviewer label, never the content or untrusted keys. The separate raw
    reader remains available and no state, ordering policy or API is changed.
    """
    raw = run_negative_results.load_complete_results(run_id)
    if raw is None:
        return None
    critiques = []
    for result in raw.critiques:
        try:
            critiques.append(_parse(result))
        except InvalidCritique as error:
            raise InvalidCritique(error.reason, result.self_label) from None
    return ValidatedResults(
        source_revision=raw.source_revision,
        inputs_sha256=raw.inputs_sha256,
        stage1_source_sha256=raw.stage1_source_sha256,
        review_policy_sha256=raw.review_policy_sha256,
        negative_prompt_source=raw.negative_prompt_source,
        provider_key=raw.provider_key,
        label_to_model=dict(raw.label_to_model),
        critiques=tuple(critiques),
    )
