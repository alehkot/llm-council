"""Read-only, paginated discovery of internal durable CLI work, not public history.

Output is bounded; the existing file store still requires scanning receipt headers
for ordering. No work is resumed or repaired. This is not the completed-run/legacy
conversation history contract and does not advertise a public API capability.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timezone
from typing import TypedDict

from . import run_admission, run_inspection, storage

EXCERPT_CHARS = 160


class RunSummary(TypedDict):
    run_id: str
    conversation_id: str
    created_at: str
    question_excerpt: str
    internal_phase: run_inspection.InternalPhase
    cursor: str


class HistoryPage(TypedDict):
    runs: list[RunSummary]
    next_cursor: str | None


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    # Admission uses utcnow().isoformat(); historical naive values are UTC.
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def _encode(key: tuple[datetime, str]) -> str:
    raw = json.dumps(
        {"version": 1, "created_at": key[0].isoformat(), "run_id": key[1]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str) -> tuple[datetime, str]:
    try:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 512:
            raise ValueError("Invalid cursor")
        raw = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
        )
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "created_at", "run_id"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or not isinstance(value["created_at"], str)
            or not isinstance(value["run_id"], str)
            or run_admission._run_id(value["run_id"]) != value["run_id"]
        ):
            raise ValueError("Invalid cursor")
        key = (_time(value["created_at"]), value["run_id"])
        if _encode(key) != cursor:
            raise ValueError("Noncanonical cursor")
        return key
    except (ValueError, TypeError, AttributeError, OverflowError, binascii.Error):
        raise ValueError("Invalid internal history cursor") from None


def list_runs(*, limit: int = 50, before: str | None = None) -> HistoryPage:
    """Newest-first summaries, ordered by UTC creation time then canonical UUID.

    Cursor is an exclusive position, not authorization or a mutation revision.
    It remains usable after its anchor is deleted. Pages are separate snapshots:
    new newer work is found by refreshing the first page; state can change between
    pages. Within a page, the existing single-process storage lock coordinates
    supported mutations. External filesystem writes remain unsupported.

    Invalid/unreadable receipts fail the page instead of silently hiding work.
    Selected entries additionally undergo the complete inspector validation; a
    corrupt checkpoint outside this page is discovered when that page is read.
    Deleted receipts and unrelated legacy conversations are never returned.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    boundary = None if before is None else _decode(before)
    with storage._storage_lock:
        try:
            paths = list(run_admission._directory().iterdir())
        except FileNotFoundError:
            return {"runs": [], "next_cursor": None}
        except OSError:
            raise run_admission.AdmissionUnavailable(
                "Run history is unreadable; repair required"
            ) from None
        records = []
        for path in paths:
            if path.suffix != ".json":
                continue
            try:
                if run_admission._run_id(path.stem) != path.stem:
                    raise ValueError("Noncanonical receipt filename")
                record = run_admission._read(path.stem)
                if record is None:
                    raise run_admission.AdmissionUnavailable(
                        "Run receipt disappeared during history read"
                    )
                if record["phase"] == "deleted":
                    continue
                key = (_time(record["created_at"]), record["run_id"])
            except (ValueError, TypeError, AttributeError, OverflowError):
                raise run_admission.AdmissionUnavailable(
                    "Run history contains an invalid receipt; repair required"
                ) from None
            if boundary is None or key < boundary:
                records.append((key, record["run_id"]))
        records.sort(reverse=True)
        selected = records[:limit]
        summaries: list[RunSummary] = []
        for _, run_id in selected:
            snapshot = run_inspection.inspect(run_id)
            if snapshot is None:
                raise run_admission.AdmissionUnavailable(
                    "Run receipt disappeared during history read"
                )
            summaries.append(
                {
                    "run_id": snapshot["run_id"],
                    "conversation_id": snapshot["conversation_id"],
                    "created_at": snapshot["created_at"],
                    "question_excerpt": snapshot["request"]["content"][:EXCERPT_CHARS],
                    "internal_phase": snapshot["internal_phase"],
                    "cursor": snapshot["cursor"],
                }
            )
        return {
            "runs": summaries,
            "next_cursor": _encode(selected[-1][0]) if len(records) > limit else None,
        }
