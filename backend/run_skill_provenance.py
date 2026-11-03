"""Internal identity guard for explicitly selected skills, not an executor.

The capture helper returns the very bytes whose identity was checked, avoiding
a later library reread. Callers must consume that snapshot, not mutable paths.
Automatic selection and pipeline provenance are not implemented here. No routes,
providers or worker scheduling call this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import refinement_execution, run_admission, run_interaction, skills, storage


class SkillProvenanceConflict(ValueError):
    """The selected skill set or saved identity is no longer safe to use."""


@dataclass(frozen=True)
class CapturedSkill:
    """Canonical in-memory content, not a dispatch/cancellation permission token.

    JSON bytes are immutable and include body, references and routing metadata.
    document() returns a detached consumer copy and performs no library reads.
    Content is deliberately excluded from repr, and is not persisted by capture.
    """

    id: str
    sha256: str
    document_json: bytes = field(repr=False)

    def document(self) -> dict[str, Any]:
        return json.loads(self.document_json)


def _capture(ids: list[str]) -> tuple[CapturedSkill, ...]:
    """Read once while the caller owns skills._skills_lock; hash returned bytes."""
    result = []
    for skill_id in ids:
        skill = skills.get_skill(skill_id)
        if (
            skill is None
            or not skill.enabled
            or skill.id != skill_id
            or skill.source != "uploaded"
        ):
            raise SkillProvenanceConflict("An explicitly selected skill is unavailable")
        body = skills.read_skill_markdown(skill_id)
        if body is None:
            raise SkillProvenanceConflict("Selected skill content is unavailable")
        references = []
        for ref in skill.references:
            text = skills.read_reference(skill_id, ref.path)
            if text is None:
                raise SkillProvenanceConflict("Selected skill reference is unavailable")
            references.append(
                {
                    "path": ref.path,
                    "text": text,
                    "chars": ref.chars,
                    "headings": ref.headings,
                    "mentions": ref.mentions,
                    "summary": ref.summary,
                    "sections": [
                        vars(section) for section in skills.reference_sections(ref)
                    ],
                }
            )
        # Include routing inputs and reference order, not filesystem paths or secrets.
        document = {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "version": skill.version,
            "body": body,
            "references": references,
        }
        encoded = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        result.append(
            CapturedSkill(skill_id, hashlib.sha256(encoded).hexdigest(), encoded)
        )
    return tuple(result)


def _identity(captured: tuple[CapturedSkill, ...]) -> dict[str, Any]:
    return {
        "version": 1,
        "skills": [{"id": item.id, "sha256": item.sha256} for item in captured],
    }


def validate_saved(record: dict[str, Any]) -> dict[str, Any] | None:
    """Validate saved identity structurally, without reading the current library."""
    if "explicit_skill_provenance" not in record:
        return None
    saved = record["explicit_skill_provenance"]
    try:
        if record["phase"] != "prepared" or not isinstance(saved, dict):
            raise ValueError("invalid phase")
        if (
            set(saved) != {"version", "skills"}
            or type(saved["version"]) is not int
            or saved["version"] != 1
        ):
            raise ValueError("invalid version")
        entries = saved["skills"]
        ids = record["request"]["skill_ids"]
        if not ids or not isinstance(entries, list) or len(entries) != len(ids):
            raise ValueError("invalid selection")
        for entry, skill_id in zip(entries, ids):
            if (
                not isinstance(entry, dict)
                or set(entry) != {"id", "sha256"}
                or entry["id"] != skill_id
                or not isinstance(entry["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
            ):
                raise ValueError("invalid identity")
    except (KeyError, TypeError, ValueError) as exc:
        raise run_admission.AdmissionUnavailable(
            "Explicit skill identity is invalid; repair required"
        ) from exc
    return {"version": 1, "skills": [dict(entry) for entry in entries]}


def bind_explicit_skills(run_id: str) -> dict[str, Any]:
    """Bind or compare explicitly requested skills; schedules and invokes nothing.

    Empty requests are rejected: the existing workflow may auto-select skills.
    Only uploaded skills are supported; external local skill sources can change
    independently of this process. Even uploaded files can be edited externally.
    The stored hashes are identities, not a guarantee of immutable later reads.
    """
    with storage._storage_lock:
        record = run_admission.get_prepared(run_id)
        ids = record["request"]["skill_ids"]
        if not ids:
            raise SkillProvenanceConflict(
                "Automatic or empty skill selection is unsupported"
            )
        # Validate adjacent checkpoints even on idempotent reads; corrupted work
        # must not be mistaken for a safe identity match.
        interaction = run_interaction._validate_record(record)
        execution = refinement_execution._validate_record(record)
        # Lock order is storage then skills; skill library never acquires storage lock.
        with skills._skills_lock:
            current = _identity(_capture(ids))
            if "explicit_skill_provenance" in record:
                saved = validate_saved(record)
                if saved != current:
                    raise SkillProvenanceConflict(
                        "Selected skill identity changed or is invalid"
                    )
                return current
            # Do not pretend an after-the-fact identity existed before any work.
            if (
                interaction.revision
                or interaction.prompts
                or execution.operations
                or "refinement_provider" in record
            ):
                raise SkillProvenanceConflict(
                    "Cannot attach identity after work has started"
                )
            record["explicit_skill_provenance"] = current
            run_admission._write(record)
            return current


def capture_explicit_skills(run_id: str) -> tuple[CapturedSkill, ...]:
    """Return content matching an already-bound identity, without changing state.

    Capture and verification use the same canonical bytes. Concurrent supported
    library mutations are serialized; independent filesystem writers are not,
    but mixed/drifted content fails the saved-digest comparison. A returned value
    remains detached if library files later change or disappear. A later executor
    must use this value without rereading paths, and must separately recheck run
    revision/cancellation immediately before guarded dispatch. This is not a
    durable copy, a full pipeline snapshot, or authorization to call a provider.
    """
    with storage._storage_lock:
        record = run_admission.get_prepared(run_id)
        saved = validate_saved(record)
        if saved is None:
            raise SkillProvenanceConflict("Explicit skill identity has not been bound")
        run_interaction._validate_record(record)
        refinement_execution._validate_record(record)
        with skills._skills_lock:
            captured = _capture(record["request"]["skill_ids"])
            if _identity(captured) != saved:
                raise SkillProvenanceConflict(
                    "Selected skill identity changed or is invalid"
                )
            return captured
