"""Durable content for explicitly selected uploaded skills; never an executor.

Identity must already be bound. Saved copies survive library changes, but do not
authorize dispatch: later execution must separately check the run and policy.
Only prompt-readable content and routing metadata are copied, not bundle files.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from pydantic import ConfigDict, Field, StrictInt, StrictStr

from . import run_admission, run_interaction, run_skill_provenance, storage
from .consultation_contract import WireModel
from .run_skill_provenance import CapturedSkill, SkillProvenanceConflict


class _FrozenDocument(WireModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class _Section(_FrozenDocument):
    section_id: StrictStr
    heading: StrictStr
    chars: Annotated[StrictInt, Field(ge=0)]
    summary: StrictStr


class _Reference(_FrozenDocument):
    path: StrictStr
    text: StrictStr = Field(repr=False)
    chars: Annotated[StrictInt, Field(ge=0)]
    headings: tuple[StrictStr, ...]
    mentions: tuple[StrictStr, ...]
    summary: StrictStr
    sections: tuple[_Section, ...]


class _Document(_FrozenDocument):
    id: StrictStr
    name: StrictStr
    description: StrictStr
    version: Annotated[StrictInt, Field(ge=1)]
    body: StrictStr = Field(repr=False)
    references: tuple[_Reference, ...]


def _encode(document: Any) -> bytes:
    """Validate the saved shape without coercing it or using today's library."""
    parsed = _Document.model_validate(document)
    if parsed.model_dump(mode="json") != document:
        raise ValueError("Skill document is not canonical")
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def validate_saved(record: dict[str, Any]) -> tuple[CapturedSkill, ...] | None:
    """Read exact copies with matching accepted identities; no library I/O."""
    if "explicit_skill_snapshot" not in record:
        return None
    try:
        identity = run_skill_provenance.validate_saved(record)
        raw = record["explicit_skill_snapshot"]
        if (
            identity is None
            or not isinstance(raw, dict)
            or set(raw) != {"version", "skills"}
            or type(raw["version"]) is not int
            or raw["version"] != 1
            or not isinstance(raw["skills"], list)
            or len(raw["skills"]) != len(identity["skills"])
        ):
            raise ValueError("Invalid skill snapshot")
        result = []
        for item, accepted in zip(raw["skills"], identity["skills"]):
            if (
                not isinstance(item, dict)
                or set(item) != {"id", "sha256", "document"}
                or item["id"] != accepted["id"]
                or item["sha256"] != accepted["sha256"]
            ):
                raise ValueError("Skill snapshot identity changed")
            encoded = _encode(item["document"])
            if (
                item["document"]["id"] != item["id"]
                or hashlib.sha256(encoded).hexdigest() != item["sha256"]
            ):
                raise ValueError("Skill snapshot content changed")
            result.append(CapturedSkill(item["id"], item["sha256"], encoded))
        return tuple(result)
    except (KeyError, TypeError, ValueError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Saved skill content is incompatible; repair required"
        ) from exc


def persist(run_id: str, *, expected_revision: int) -> tuple[CapturedSkill, ...]:
    """Persist matching content once; repeats do not read the current library.

    Initial capture follows the existing storage-then-skills lock order. A stale,
    cancelled, deleted or corrupt run cannot write or receive a successful repeat.
    Identity binding is never implicit. No prompt/worker revision is advanced.
    """
    from . import run_inspection

    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    with storage._storage_lock:
        run_inspection.inspect(run_id)
        record, interaction = run_interaction._load(run_id)
        if interaction.revision != expected_revision:
            raise run_interaction.PromptConflict("Skill snapshot revision changed")
        saved = validate_saved(record)
        if saved is not None:
            return saved
        captured = run_skill_provenance.capture_explicit_skills(run_id)
        record["explicit_skill_snapshot"] = {
            "version": 1,
            "skills": [
                {"id": item.id, "sha256": item.sha256, "document": item.document()}
                for item in captured
            ],
        }
        # Validate the same bytes that will be persisted, not a subsequent read.
        try:
            checked = validate_saved(record)
        except run_admission.AdmissionUnavailable as exc:
            raise SkillProvenanceConflict(
                "Captured skill content is unsupported"
            ) from exc
        run_admission._write(record)
        return checked


def load(run_id: str) -> tuple[CapturedSkill, ...]:
    """Read validated historical copies, including cancelled runs; never repair.

    This is deliberately not a runnable-state check. Callers must not use a
    returned value as authorization to dispatch, resume or repeat model work.
    """
    from . import run_inspection

    with storage._storage_lock:
        run_inspection.inspect(run_id)
        record = run_admission._read(run_id)
        saved = validate_saved(record) if record is not None else None
        if saved is None:
            raise SkillProvenanceConflict("Explicit skill content has not been saved")
        return saved
