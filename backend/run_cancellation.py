"""Durable cancellation for internal CLI operations, not legacy web workers.

The marker prevents new work before local task cancellation is requested. Saved
partial results are retained. A provider may already have accepted a request;
local cancellation neither cancels remote billing nor proves remote work stopped.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from . import run_admission, storage

# Shared by existing internal executors in this backend process only. All access
# is under the storage lock; callbacks run on the owning task's event loop.
_tasks: dict[tuple[str, str], asyncio.Task[Any]] = {}
_signalled: set[tuple[str, str]] = set()


def _register(run_id: str, attempt: str, task: asyncio.Task[Any]) -> None:
    """Called under storage lock before the executor's first await."""
    _tasks[(run_id, attempt)] = task


def _settle(record: dict[str, Any]) -> dict[str, Any] | None:
    cancellation = record.get("cancellation")
    if cancellation is None:
        return None
    if cancellation["phase"] == "cancelling" and not any(
        identity[0] == record["run_id"] for identity in _tasks
    ):
        cancellation["phase"] = "cancelled"
        cancellation["completed_at"] = datetime.utcnow().isoformat()
        run_admission._write(record)
    return dict(cancellation)


def _release(run_id: str, attempt: str) -> None:
    """Release ownership after local provider cleanup; never recreate deletion."""
    _tasks.pop((run_id, attempt), None)
    _signalled.discard((run_id, attempt))
    record = run_admission.get(run_id)
    if record is not None and record["phase"] == "prepared":
        _settle(record)


def inspect(run_id: str) -> dict[str, Any] | None:
    """Return cancellation state, settling orphaned cancellation after restart.

    No provider dispatch or retry is performed. Once no registered local task
    remains, cancellation is terminal even if a remote operation is uncertain.
    """
    with storage._storage_lock:
        record = run_admission.get(run_id)
        if record is None or record["phase"] != "prepared":
            raise run_admission.AdmissionUnavailable("Prepared run is unavailable")
        return _settle(record)


def request(run_id: str) -> dict[str, Any]:
    """Idempotently cancel this prepared run only, preserving all saved evidence.

    Persist before signalling. A failed persistence attempt never signals tasks.
    Repeated requests do not repeatedly interrupt a task's cancellation cleanup.
    The caller can inspect until cleanup finishes; uncooperative tasks keep the
    run cancelling. No await or event-loop operation happens under storage lock.
    """
    targets = []
    with storage._storage_lock:
        record = run_admission.get(run_id)
        if record is not None and record["phase"] == "deleted":
            raise run_admission.AdmissionDeleted("Run was deleted")
        if record is None or record["phase"] != "prepared":
            raise run_admission.AdmissionUnavailable("Run has not completed admission")
        run_admission._validate_conversation(record)
        if "cancellation" not in record:
            record["cancellation"] = {
                "phase": "cancelling",
                "requested_at": datetime.utcnow().isoformat(),
                "completed_at": None,
            }
            run_admission._write(record)
        targets = [
            (identity, task)
            for identity, task in _tasks.items()
            if identity[0] == record["run_id"] and identity not in _signalled
        ]
        result = _settle(record)
    for identity, task in targets:
        try:
            task.get_loop().call_soon_threadsafe(_signal, identity, task)
        except RuntimeError:
            # A closing loop cannot run more work. Keep cancelling until ownership
            # cleanup or process restart proves that no local task remains.
            pass
    assert result is not None
    return result


def _signal(identity: tuple[str, str], task: asyncio.Task[Any]) -> None:
    # Runs on the dedicated provider task's own loop. Recheck ownership so a
    # delayed callback cannot cancel cleanup or unrelated caller work.
    with storage._storage_lock:
        if _tasks.get(identity) is not task or identity in _signalled:
            return
        _signalled.add(identity)
    task.cancel()
