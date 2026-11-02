"""Internal CLI admission records; no HTTP operation or worker scheduling yet.

Receipts live separately from the disposable execution journal. All operations
share the conversation storage lock, including deletion. The manager takes its
own lock first. This is for the existing single-backend-process architecture.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from . import storage
from .consultation_contract import CreateRunRequest


class AdmissionConflict(ValueError):
    """The identity was already accepted with a different payload."""


class AdmissionDeleted(ValueError):
    """The identity was deleted and must never create work again."""


class AdmissionCancelled(ValueError):
    """This run was cancelled; retained work is not runnable."""


class AdmissionUnavailable(RuntimeError):
    """Existing durable state needs repair; do not replace or resubmit it."""


def _run_id(run_id: str) -> str:
    return str(UUID(run_id))


def _directory() -> Path:
    return Path(storage.DATA_DIR).parent / "run_receipts"


def _path(run_id: str) -> Path:
    return _directory() / f"{_run_id(run_id)}.json"


def _read(run_id: str) -> dict[str, Any] | None:
    path = _path(run_id)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise AdmissionUnavailable(
            "Run receipt is unreadable; repair required"
        ) from exc
    if (
        not isinstance(record, dict)
        or record.get("run_id") != _run_id(run_id)
        or record.get("conversation_id") != f"cli-{_run_id(run_id)}"
        or record.get("phase") not in ("initializing", "prepared", "deleted")
        or type(record.get("version")) is not int
        or record.get("version") != 1
    ):
        raise AdmissionUnavailable("Run receipt is invalid; repair required")
    if record["phase"] != "deleted":
        try:
            if "cancellation" in record:
                cancellation = record["cancellation"]
                if (
                    not isinstance(cancellation, dict)
                    or set(cancellation) != {"phase", "requested_at", "completed_at"}
                    or cancellation["phase"] not in ("cancelling", "cancelled")
                    or not isinstance(cancellation["requested_at"], str)
                    or record["phase"] != "prepared"
                ):
                    raise ValueError("Invalid cancellation marker")
                datetime.fromisoformat(cancellation["requested_at"])
                if cancellation["phase"] == "cancelled":
                    if not isinstance(cancellation["completed_at"], str):
                        raise ValueError("Invalid cancellation completion")
                    datetime.fromisoformat(cancellation["completed_at"])
                elif cancellation["completed_at"] is not None:
                    raise ValueError("Cancellation is still active")
            # Validate the saved schema, not today's configurable admission
            # limits: lowering a prompt/skill limit must not hide older receipts.
            payload = record["request"]
            if not isinstance(payload, dict) or set(payload) != {
                "content",
                "negative_prompt",
                "skill_ids",
                "refine",
                "interaction_policy",
                "recovery_policy",
            }:
                raise ValueError("Noncanonical request")
            if (
                not isinstance(payload["content"], str)
                or not payload["content"].strip()
                or (
                    payload["negative_prompt"] is not None
                    and not isinstance(payload["negative_prompt"], str)
                )
                or type(payload["refine"]) is not bool
                or payload["interaction_policy"] != "persistent"
                or payload["recovery_policy"] != "explicit_retry"
                or not isinstance(payload["skill_ids"], list)
            ):
                raise ValueError("Invalid accepted request")
            for skill_id in payload["skill_ids"]:
                storage.validate_storage_id(skill_id, "skill_id")
            if len(set(payload["skill_ids"])) != len(payload["skill_ids"]):
                raise ValueError("Duplicate accepted skills")
            if not isinstance(record["created_at"], str):
                raise ValueError("Invalid timestamp")
            datetime.fromisoformat(record["created_at"])
            if (
                type(record.get("assistant_index")) is not int
                or record["assistant_index"] != 1
            ):
                raise ValueError("Invalid assistant reference")
        except (KeyError, ValueError, TypeError) as exc:
            raise AdmissionUnavailable("Run receipt payload is invalid") from exc
    return record


def _write(record: dict[str, Any]) -> None:
    storage._write_json_file(str(_path(record["run_id"])), record)


def _conversation(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["conversation_id"],
        "created_at": record["created_at"],
        "title": "New Conversation",
        "cli_admission": record["run_id"],
        "messages": [
            {"role": "user", "content": record["request"]["content"]},
            {
                "role": "assistant",
                "stage1": None,
                "negative": None,
                "stage2": None,
                "stage3": None,
                "metadata": {
                    "consultation_id": record["run_id"],
                    # Not a legacy resumable status: startup must not run it.
                    "status": "prepared",
                },
                "trace": None,
            },
        ],
    }


def prepare(run_id: str, request: CreateRunRequest) -> dict[str, Any]:
    """Persist one canonical admission and its conversation, but schedule nothing.

    This internal primitive requires a validated request. Skill resolution and
    effective execution provenance belong to future admission integration, before
    exposing an endpoint. Repeated preparation compares the accepted payload,
    without consulting changing runtime settings or provider availability.
    """
    canonical_id = _run_id(run_id)
    payload = request.model_dump(mode="json")
    with storage._storage_lock:
        record = _read(canonical_id)
        if record is not None:
            if record["phase"] == "deleted":
                raise AdmissionDeleted(
                    "Run was deleted; choose a new identity explicitly"
                )
            if record["request"] != payload:
                raise AdmissionConflict("Run ID already has a different request")
        else:
            conversation_id = f"cli-{canonical_id}"
            if Path(storage.get_conversation_path(conversation_id)).exists():
                raise AdmissionUnavailable(
                    "Conversation exists without its run receipt"
                )
            record = {
                "version": 1,
                "run_id": canonical_id,
                "conversation_id": conversation_id,
                "assistant_index": 1,
                "phase": "initializing",
                "created_at": datetime.utcnow().isoformat(),
                "request": payload,
            }
            # Durable identity precedes conversation creation and any future work.
            _write(record)

        path = Path(storage.get_conversation_path(record["conversation_id"]))
        if record["phase"] == "initializing":
            expected = _conversation(record)
            if path.exists():
                try:
                    actual = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise AdmissionUnavailable(
                        "Initializing conversation is unreadable"
                    ) from exc
                if actual != expected:
                    raise AdmissionUnavailable(
                        "Initializing conversation does not match receipt"
                    )
            else:
                storage._write_json_file(str(path), expected)
            record["phase"] = "prepared"
            _write(record)
        else:
            _validate_conversation(record)
        return record


def _validate_conversation(record: dict[str, Any]) -> None:
    canonical_id = record["run_id"]
    path = Path(storage.get_conversation_path(record["conversation_id"]))
    # Never recreate externally removed/damaged accepted work. Deletion
    # through storage writes a tombstone before removing the conversation.
    try:
        conversation = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdmissionUnavailable("Prepared conversation is unavailable") from exc
    if (
        not isinstance(conversation, dict)
        or conversation.get("id") != record["conversation_id"]
        or conversation.get("cli_admission") != canonical_id
    ):
        raise AdmissionUnavailable("Prepared conversation is unavailable")
    messages = conversation.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) <= record["assistant_index"]
        or not isinstance(messages[1], dict)
        or messages[1].get("role") != "assistant"
        or not isinstance(messages[1].get("metadata"), dict)
        or messages[1]["metadata"].get("consultation_id") != canonical_id
    ):
        raise AdmissionUnavailable("Prepared assistant reference is unavailable")


def get_prepared(run_id: str) -> dict[str, Any]:
    """Require an intact prepared admission before a durable state mutation."""
    with storage._storage_lock:
        record = _read(run_id)
        if record is not None and record["phase"] == "deleted":
            raise AdmissionDeleted("Run was deleted")
        if record is None or record["phase"] != "prepared":
            raise AdmissionUnavailable("Run has not completed admission")
        _validate_conversation(record)
        if "cancellation" in record:
            raise AdmissionCancelled("Run cancellation has been requested")
        return record


def get(run_id: str) -> dict[str, Any] | None:
    """Read a detached internal receipt, including deletion markers; no writes."""
    with storage._storage_lock:
        return _read(run_id)


def list_receipts(limit: int = 50) -> list[dict[str, Any]]:
    """Return newest internal receipts, excluding deleted IDs, with bounded output."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    with storage._storage_lock:
        records = []
        for path in _directory().glob("*.json"):
            record = _read(path.stem)
            if record is not None and record["phase"] != "deleted":
                records.append(record)
        return sorted(
            records,
            key=lambda record: (record["created_at"], record["run_id"]),
            reverse=True,
        )[:limit]


def tombstone_conversation(conversation_id: str) -> None:
    """Retain only IDs/deletion time before deleting prepared or partial work."""
    if not conversation_id.startswith("cli-"):
        return
    try:
        run_id = _run_id(conversation_id[4:])
    except (ValueError, TypeError, AttributeError):
        return
    with storage._storage_lock:
        record = _read(run_id)
        if (
            record is None
            or record["phase"] == "deleted"
            or record["conversation_id"] != conversation_id
        ):
            return
        _write(
            {
                "version": 1,
                "run_id": run_id,
                "conversation_id": record["conversation_id"],
                "phase": "deleted",
                "deleted_at": datetime.utcnow().isoformat(),
            }
        )


def tombstone_all() -> None:
    """Include admissions whose conversation initialization has not finished."""
    with storage._storage_lock:
        for path in _directory().glob("*.json"):
            record = _read(path.stem)
            if record is not None:
                tombstone_conversation(record["conversation_id"])
