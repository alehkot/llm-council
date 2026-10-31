"""Opt-in live API-to-disk council check; never imported by test discovery."""

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def check_result(
    conversation: dict[str, Any], run_dir: Path, model_count: int
) -> dict[str, bool]:
    messages = [
        m for m in conversation.get("messages", []) if m.get("role") == "assistant"
    ]
    message = messages[-1] if messages else {}
    metadata = message.get("metadata") or {}
    stage1 = message.get("stage1") or []
    stage2 = message.get("stage2") or []
    negative = message.get("negative") or {}
    revisions = (metadata.get("deliberation") or {}).get("results") or []
    current = (message.get("stage3") or {}).get("current") or {}
    results = list((run_dir / "synthesis").glob("*/result.md"))
    return {
        "consultation_complete": metadata.get("status") == "complete",
        "all_council_members_answered": len(stage1) == model_count
        and all(not r.get("failed") and r.get("response", "").strip() for r in stage1),
        "gap_reviews_present": len(negative.get("evaluations") or []) == model_count,
        "all_peer_rankings_valid": len(stage2) == model_count
        and all(r.get("validation_status") == "valid" for r in stage2),
        "deliberation_present": len(revisions) == model_count
        and all(r.get("revised", "").strip() for r in revisions),
        "skills_selected": bool((metadata.get("skills") or {}).get("resolved")),
        "codex_astra_synthesized": current.get("model") == "codex-cli/gpt-6-astra"
        and bool(current.get("response", "").strip()),
        "disk_result_matches_saved_answer": len(results) == 1
        and results[0].read_text() == current.get("response"),
    }


def skill_pack() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "investment-decision/SKILL.md",
            "---\nname: investment-decision\ndescription: Compare capacity investments, rentals, and reversible experiments under uncertain demand.\n---\nSeparate sunk and incremental costs. Model scenarios and state assumptions.\nRead [decision checklist](references/checklist.md) for equipment decisions.\n",
        )
        archive.writestr(
            "investment-decision/references/checklist.md",
            "# Capacity decision checklist\nCap sales at available capacity. Charge labor only on days equipment is used.\nShow one-time and recurring cash flows separately. Distinguish trial break-even\nfrom long-run purchase payback. Do not extrapolate seasonal demand without evidence.\n",
        )
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-live",
        action="store_true",
        help="Authorize real provider calls and Codex subscription usage for this run",
    )
    parser.add_argument(
        "--problem-file",
        type=Path,
        default=ROOT / "scripts/examples/bakery-expansion.md",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data/live-e2e",
        help="Parent directory; each run gets a new UUID subdirectory",
    )
    args = parser.parse_args()
    if not args.run_live:
        parser.error(
            "Live execution is opt-in. Add --run-live to make real provider and CLI calls."
        )
    question = args.problem_file.expanduser().resolve().read_text().strip()
    if not question:
        parser.error("Problem file is empty")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    run_dir = args.output_dir.expanduser().resolve() / str(uuid.uuid4())
    run_dir.mkdir(parents=True, mode=0o700)
    (run_dir / "problem.md").write_text(question)
    # Set before importing backend modules; never rewrite the project's .env.
    os.environ.update(
        {
            "DATA_DIR": str(run_dir / "conversations"),
            "SYNTHESIS_AGENT_DIR": str(run_dir / "synthesis"),
            "SYNTHESIS_BACKEND": "codex",
            "SYNTHESIS_AGENT_MODEL": "gpt-6-astra",
            "SYNTHESIS_AGENT_EFFORT": "xhigh",
            "NEGATIVE_MODE_ENABLED": "true",
            "DELIBERATION_ENABLED": "true",
            "INTERACTIVE_MODE_ENABLED": "false",
            "PROMPT_REFINEMENT_ENABLED": "false",
            "SKILLS_ENABLED": "true",
            "SKILLS_AUTO_SELECT_ENABLED": "true",
            "SKILLS_REFERENCE_SELECTION_ENABLED": "true",
        }
    )
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient

    from backend.config import API_AUTH_KEY, COUNCIL_MODELS, SKILLS_TYPESAFE_ENABLED
    from backend.main import app, consultation_manager

    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "council_models": COUNCIL_MODELS,
        "synthesis": "codex/gpt-6-astra/xhigh",
        "jev_enabled": SKILLS_TYPESAFE_ENABLED,
        "checks": {},
        "passed": False,
    }
    print(f"Live E2E artifacts: {run_dir}", flush=True)
    print(f"Council: {', '.join(COUNCIL_MODELS)}", flush=True)
    try:
        with TestClient(app, headers={"X-API-Key": API_AUTH_KEY}) as client:
            uploaded = client.post(
                "/api/skills?filename=investment-decision.zip",
                content=skill_pack(),
                headers={"Content-Type": "application/zip"},
            )
            uploaded.raise_for_status()
            created = client.post("/api/conversations", json={})
            created.raise_for_status()
            conversation_id = created.json()["id"]
            report["conversation_id"] = conversation_id
            submitted = client.post(
                f"/api/conversations/{conversation_id}/message",
                json={"content": question},
            )
            submitted.raise_for_status()
            cursor = None
            previous_status = None
            while True:
                params = {"wait": 10}
                if cursor:
                    params["cursor"] = cursor
                response = client.get(
                    f"/api/conversations/{conversation_id}/consultation/status",
                    params=params,
                )
                response.raise_for_status()
                snapshot = response.json()
                cursor = snapshot["cursor"]
                status = snapshot["status"]
                if status != previous_status:
                    print(f"Stage: {status}", flush=True)
                    previous_status = status
                if status in {"complete", "failed"}:
                    break
            saved = client.get(f"/api/conversations/{conversation_id}")
            saved.raise_for_status()
            conversation = saved.json()
            (run_dir / "conversation.json").write_text(
                json.dumps(conversation, indent=2, ensure_ascii=False)
            )
            report["checks"] = check_result(conversation, run_dir, len(COUNCIL_MODELS))
            report["passed"] = all(report["checks"].values())
            if snapshot.get("error"):
                report["error"] = snapshot["error"]
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = type(exc).__name__
        consultation_manager.cancel_all_consultations()
        # This process owns only the isolated live test's worker loops.
        with consultation_manager._lock:
            loops = [
                s.loop for s in consultation_manager._consultations.values() if s.loop
            ]
        for loop in loops:

            def cancel_tasks(loop: asyncio.AbstractEventLoop = loop) -> None:
                for task in asyncio.all_tasks(loop):
                    task.cancel()

            try:
                loop.call_soon_threadsafe(cancel_tasks)
            except RuntimeError:
                pass
    finally:
        consultation_manager.shutdown(wait=True)
        report["ended_at"] = datetime.now(timezone.utc).isoformat()
        (run_dir / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"Report: {run_dir / 'report.json'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
