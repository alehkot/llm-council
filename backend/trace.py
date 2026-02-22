"""Trace collection helpers for debugging LLM flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def _utc_iso() -> str:
    return datetime.utcnow().isoformat()


@dataclass
class TraceCollector:
    """Collect ordered trace events for a single conversation flow."""

    flow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = field(default_factory=_utc_iso)
    ended_at: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)

    def add_event(self, kind: str, **data: Any) -> str:
        event_id = str(uuid.uuid4())
        payload = {
            "id": event_id,
            "kind": kind,
            "timestamp": _utc_iso(),
            **data,
        }
        self.events.append(payload)
        return event_id

    def begin_stage(self, stage: str, **data: Any) -> str:
        return self.add_event("stage_start", stage=stage, **data)

    def end_stage(self, stage: str, **data: Any) -> str:
        return self.add_event("stage_complete", stage=stage, **data)

    def finish(self) -> None:
        self.ended_at = _utc_iso()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "events": self.events,
        }
