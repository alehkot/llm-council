"""Durable persistence for queued and in-flight consultations."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR


_CONSULTATION_DIR = Path(DATA_DIR).parent / "consultations"


def _ensure_consultation_dir() -> None:
    _CONSULTATION_DIR.mkdir(parents=True, exist_ok=True)


def _job_path(consultation_id: str) -> Path:
    return _CONSULTATION_DIR / f"{consultation_id}.json"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def create_job(
    consultation_id: str,
    conversation_id: str,
    content: str,
    negative_prompt: Optional[str],
    status: str,
    assistant_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a durable consultation record."""
    timestamp = datetime.utcnow().isoformat()
    payload = {
        "consultation_id": consultation_id,
        "conversation_id": conversation_id,
        "content": content,
        "negative_prompt": negative_prompt,
        "status": status,
        "assistant_index": assistant_index,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    _ensure_consultation_dir()
    _write_json(_job_path(consultation_id), payload)
    return payload


def get_job(consultation_id: str) -> Optional[Dict[str, Any]]:
    """Load a persisted consultation record."""
    path = _job_path(consultation_id)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: Failed to read consultation job {consultation_id}: {exc}")
        return None


def update_job(consultation_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update a persisted consultation record."""
    current = get_job(consultation_id)
    if current is None:
        return None

    current.update(updates)
    current["updated_at"] = datetime.utcnow().isoformat()
    _write_json(_job_path(consultation_id), current)
    return current


def delete_job(consultation_id: str) -> None:
    """Delete a consultation record if it exists."""
    try:
        _job_path(consultation_id).unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        print(f"Warning: Failed to delete consultation job {consultation_id}: {exc}")


def list_jobs() -> List[Dict[str, Any]]:
    """List all durable consultation records."""
    _ensure_consultation_dir()
    jobs: List[Dict[str, Any]] = []
    for path in sorted(_CONSULTATION_DIR.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                jobs.append(json.load(handle))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: Failed to read consultation job {path.name}: {exc}")
    return jobs
