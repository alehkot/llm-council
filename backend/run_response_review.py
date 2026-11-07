"""Read-only preparation of the next human review from complete Stage 1 evidence.

This is not a prompt instance, persisted checkpoint or permission to advance.
A future mutating coordinator must recheck current state and causal provenance.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StrictInt, StrictStr

from . import (
    run_admission,
    run_interaction,
    run_stage1_inputs,
    run_stage1_results,
    storage,
)
from .consultation_contract import PromptSchema, WireModel
from .council import build_label_mapping
from .interactive import get_response_review_prompt


class ReviewPreparation(WireModel):
    """Detached historical answers and proposed schema, never an answer receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[1] = 1
    run_id: UUID
    source_revision: Annotated[StrictInt, Field(ge=1)]
    source_sha256: run_stage1_inputs.Digest
    label_to_model: dict[StrictStr, StrictStr]
    results: tuple[run_stage1_results.MemberResult, ...] = Field(repr=False)
    prompt: PromptSchema = Field(repr=False)


def prepare(run_id: str) -> ReviewPreparation | None:
    """Preview exact complete answers and the existing response-review schema.

    Incomplete rosters return None, without a partial quorum. The source digest
    binds ordered text and original per-chunk attempt/request provenance; it is
    an identity hint for a future checkpoint, not retry/dispatch authorization.
    Configuration drift does not change saved results. Cancellation, deletion
    or corrupt source evidence fails closed. No settings, library or
    provider is consulted, and no prompt instance or interaction is written.
    """
    run_id = str(UUID(run_id))
    with storage._storage_lock:
        run_admission.get_prepared(run_id)
        results = run_stage1_results.load_complete_results(run_id)
        if results is None:
            return None
        return _from_results(run_id, results)


def _from_results(
    run_id: str,
    results: tuple[run_stage1_results.MemberResult, ...],
    *,
    prompt: PromptSchema | None = None,
) -> ReviewPreparation:
    # Preserve the legacy labels/options and saved roster order. Answer text
    # is separate from the prompt schema, never copied into option labels.
    responses = [
        {"model": result.model, "response": result.content} for result in results
    ]
    _, labels = build_label_mapping(responses)
    if prompt is None:
        prompt = PromptSchema.model_validate(
            get_response_review_prompt(responses, labels).to_dict()
        )
    source = {
        "version": 1,
        "run_id": run_id,
        "results": [result.model_dump(mode="json") for result in results],
    }
    digest = hashlib.sha256(
        json.dumps(
            source,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return ReviewPreparation(
        run_id=UUID(run_id),
        source_revision=results[0].source_revision,
        source_sha256=digest,
        label_to_model=labels,
        results=results,
        prompt=prompt,
    )


def _prepare_record(
    record: dict[str, Any],
    interaction: run_interaction.InteractionState,
    *,
    prompt: PromptSchema | None = None,
) -> ReviewPreparation | None:
    """Prepare from validated historical state without recursive inspection."""
    inputs, initial, continuation = run_stage1_results._validated_states(
        record, interaction
    )
    results = []
    for request in inputs.requests:
        result = run_stage1_results._complete_result(
            request.model, inputs, initial, continuation
        )
        if result is None:
            return None
        results.append(result)
    return _from_results(record["run_id"], tuple(results), prompt=prompt)
