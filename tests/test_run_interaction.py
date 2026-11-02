"""Offline durable prompt boundaries; no workflow/provider is executed."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import run_admission, storage
from backend import run_interaction as interaction
from backend.consultation_contract import (
    AnswerRequest,
    CreateRunRequest,
    PromptInstance,
)

RUN = "12345678-1234-4234-8234-123456789abc"


def prompt(**changes: Any) -> PromptInstance:
    schema = {
        "prompt_id": "refinement",
        "prompt_type": "prompt_refinement",
        "title": "Synthetic question",
        "description": "Choose explicitly",
        "skip_allowed": False,
        "fields": [
            {"id": "text", "type": "text", "label": "Text", "required": True},
            {
                "id": "select",
                "type": "select",
                "label": "Select",
                "required": True,
                "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
            },
            {
                "id": "multi",
                "type": "multi_select",
                "label": "Multi",
                "required": True,
                "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
            },
            {"id": "check", "type": "checkbox", "label": "Check", "required": True},
            {
                "id": "optional",
                "type": "text",
                "label": "Optional",
                "default": "display only",
            },
        ],
    }
    schema.update(changes)
    return PromptInstance(run_id=RUN, instance_id=uuid4(), prompt=schema)


def continuation(**state: Any) -> interaction.Continuation:
    return interaction.Continuation(
        point="refinement_answer", state=state or {"iteration": 1, "suggestion": "Тест"}
    )


def answer(**changes: Any) -> AnswerRequest:
    values = {
        "text": "  exact text\n",
        "select": "a",
        "multi": ["b", "a"],
        "check": False,
    }
    values.update(changes)
    return AnswerRequest(responses=values)


class RunInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(storage, "DATA_DIR", str(self.root / "conversations"))
        patcher.start()
        self.addCleanup(patcher.stop)
        run_admission.prepare(
            RUN,
            CreateRunRequest(
                content="Synthetic input",
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        self.instance = prompt()
        self.saved = continuation()

    def put(self) -> dict:
        return interaction.put_prompt(
            RUN, self.instance, self.saved, expected_revision=0
        )

    def path(self) -> Path:
        return self.root / "run_receipts" / f"{RUN}.json"

    def test_exact_schema_and_continuation_detached_and_read_only(self) -> None:
        result = self.put()
        raw = self.path().read_bytes()
        result["prompts"][0]["continuation"]["state"]["iteration"] = 99
        self.saved.state["iteration"] = 100
        self.instance.prompt.title = "changed"
        actual = interaction.get(RUN)
        self.assertEqual(actual["prompts"][0]["continuation"]["state"]["iteration"], 1)
        self.assertEqual(
            actual["prompts"][0]["instance"]["prompt"]["title"], "Synthetic question"
        )
        self.assertEqual(self.path().read_bytes(), raw)

    def test_first_answer_preserves_order_whitespace_false_and_omission(self) -> None:
        self.put()
        result = interaction.answer(RUN, str(self.instance.instance_id), answer())
        self.assertEqual(result["phase"], "answered")
        self.assertEqual(result["answer"], answer().model_dump(mode="json"))
        self.assertNotIn("optional", result["answer"]["responses"])
        self.assertEqual(interaction.get(RUN)["revision"], 2)

    def test_retries_after_next_prompt_do_not_change_active_instance(self) -> None:
        self.put()
        response = interaction.answer(RUN, str(self.instance.instance_id), answer())
        next_state = continuation(iteration=2)
        interaction.advance(RUN, str(self.instance.instance_id), next_state)
        other = prompt()
        interaction.put_prompt(RUN, other, next_state, expected_revision=3)
        raw = self.path().read_bytes()
        replay = interaction.answer(RUN, str(self.instance.instance_id), answer())
        self.assertEqual(replay["answer"], response["answer"])
        self.assertEqual(replay["phase"], "consumed")
        interaction.advance(RUN, str(self.instance.instance_id), next_state)
        self.put()
        self.assertEqual(self.path().read_bytes(), raw)
        self.assertEqual(interaction.get(RUN)["prompts"][-1]["phase"], "waiting")
        with self.assertRaises(interaction.PromptConflict):
            interaction.answer(
                RUN, str(self.instance.instance_id), answer(text="changed")
            )
        with self.assertRaises(interaction.PromptConflict):
            interaction.advance(
                RUN, str(self.instance.instance_id), continuation(iteration=3)
            )

    def test_continuation_retry_distinguishes_json_value_types(self) -> None:
        self.saved = continuation(enabled=True)
        self.put()
        for value in (1, 1.0):
            with (
                self.subTest(value=value),
                self.assertRaises(interaction.PromptConflict),
            ):
                interaction.put_prompt(
                    RUN, self.instance, continuation(enabled=value), expected_revision=0
                )
        interaction.answer(RUN, str(self.instance.instance_id), answer())
        interaction.advance(RUN, str(self.instance.instance_id), continuation(value=1))
        for value in (True, 1.0):
            with (
                self.subTest(value=value),
                self.assertRaises(interaction.PromptConflict),
            ):
                interaction.advance(
                    RUN, str(self.instance.instance_id), continuation(value=value)
                )

    def test_revision_and_waiting_boundary_block_stale_workers(self) -> None:
        self.put()
        with self.assertRaises(interaction.PromptConflict):
            interaction.put_prompt(RUN, prompt(), self.saved, expected_revision=0)
        with self.assertRaises(interaction.PromptConflict):
            interaction.put_prompt(RUN, prompt(), self.saved, expected_revision=1)
        with self.assertRaises(interaction.PromptConflict):
            interaction.advance(RUN, str(self.instance.instance_id), self.saved)
        interaction.answer(RUN, str(self.instance.instance_id), answer())
        with self.assertRaises(interaction.PromptConflict):
            interaction.put_prompt(RUN, prompt(), self.saved, expected_revision=2)
        interaction.advance(RUN, str(self.instance.instance_id), self.saved)
        with self.assertRaises(interaction.PromptConflict):
            interaction.put_prompt(RUN, prompt(), self.saved, expected_revision=2)
        with self.assertRaises(ValueError):
            interaction.put_prompt(RUN, prompt(), self.saved, expected_revision=True)

    def test_known_instance_cannot_change_schema_or_checkpoint(self) -> None:
        self.put()
        self.instance.prompt.title = "Changed"
        with self.assertRaises(interaction.PromptConflict):
            self.put()
        self.instance.prompt.title = "Synthetic question"
        self.saved.state["iteration"] = 2
        with self.assertRaises(interaction.PromptConflict):
            self.put()

    def test_all_field_validation_rejects_without_writing(self) -> None:
        self.put()
        raw = self.path().read_bytes()
        for bad in (
            answer(text=""),
            answer(text="  "),
            answer(text=False),
            answer(select="unknown"),
            answer(select=True),
            answer(multi=[]),
            answer(multi=["a", "a"]),
            answer(multi=["unknown"]),
            answer(multi="a"),
            answer(check="false"),
            answer(unknown="extra"),
            AnswerRequest(),
            AnswerRequest(skipped=True),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                interaction.answer(RUN, str(self.instance.instance_id), bad)
            self.assertEqual(self.path().read_bytes(), raw)

    def test_explicit_skip_allowed_and_retained(self) -> None:
        self.instance = prompt(skip_allowed=True)
        self.put()
        result = interaction.answer(
            RUN, str(self.instance.instance_id), AnswerRequest(skipped=True)
        )
        self.assertEqual(result["answer"], {"responses": {}, "skipped": True})
        with self.assertRaises(interaction.PromptConflict):
            interaction.answer(RUN, str(self.instance.instance_id), answer())

    def test_optional_omissions_and_empty_values_not_defaulted(self) -> None:
        self.instance = prompt(
            fields=[
                {"id": "text", "label": "Text", "type": "text", "default": "display"}
            ]
        )
        self.put()
        self.assertEqual(
            interaction.answer(RUN, str(self.instance.instance_id), AnswerRequest())[
                "answer"
            ]["responses"],
            {},
        )

    def test_unknown_instance_and_cross_run_rejected(self) -> None:
        self.put()
        for operation in (
            lambda: interaction.answer(RUN, str(uuid4()), answer()),
            lambda: interaction.advance(RUN, str(uuid4()), self.saved),
        ):
            with self.assertRaises(interaction.PromptConflict):
                operation()
        self.instance.run_id = uuid4()
        with self.assertRaises(ValueError):
            self.put()

    def test_ambiguous_schemas_and_bad_defaults_rejected(self) -> None:
        base = prompt().prompt.model_dump(mode="json")
        for change in (
            lambda s: s["fields"].append(s["fields"][0].copy()),
            lambda s: s["fields"][1]["options"].append(
                {"value": "a", "label": "duplicate"}
            ),
            lambda s: s["fields"][1].update(options=[]),
            lambda s: s["fields"][0].update(options=[{"value": "a", "label": "A"}]),
            lambda s: s["fields"][1].update(default="unknown"),
        ):
            schema = json.loads(json.dumps(base))
            change(schema)
            self.instance = prompt(**schema)
            with self.assertRaises(ValueError):
                self.put()
        self.assertEqual(interaction.get(RUN)["revision"], 0)

    def test_non_json_state_rejected_including_construct_bypass(self) -> None:
        for bad in (
            {"x": float("nan")},
            {"x": float("inf")},
            {"x": (1, 2)},
            {"x": {1: "bad"}},
            {"x": object()},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                continuation(**bad)
            invalid = interaction.Continuation.model_construct(point="test", state=bad)
            with self.assertRaises(ValueError):
                interaction.put_prompt(RUN, self.instance, invalid, expected_revision=0)

    def test_concurrent_first_answer_has_one_winner(self) -> None:
        self.put()
        barrier = threading.Barrier(12)

        def submit(index: int) -> dict | None:
            barrier.wait()
            try:
                return interaction.answer(
                    RUN, str(self.instance.instance_id), answer(text=str(index))
                )
            except interaction.PromptConflict:
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(submit, range(12)))
        winners = [item for item in results if item]
        self.assertEqual(len(winners), 1)
        self.assertEqual(interaction.get(RUN)["revision"], 2)
        self.assertEqual(
            interaction.get(RUN)["prompts"][0]["answer"], winners[0]["answer"]
        )

    def test_write_failure_before_acceptance_can_retry(self) -> None:
        self.put()
        raw = self.path().read_bytes()
        with patch.object(
            run_admission, "_write", side_effect=OSError("synthetic failure")
        ):
            with self.assertRaises(OSError):
                interaction.answer(RUN, str(self.instance.instance_id), answer())
        self.assertEqual(self.path().read_bytes(), raw)
        self.assertEqual(interaction.get(RUN)["prompts"][0]["phase"], "waiting")
        interaction.answer(RUN, str(self.instance.instance_id), answer())

    def test_lost_acknowledgment_after_write_replays_first_answer(self) -> None:
        self.put()
        original = run_admission._write

        def write_then_fail(record: dict) -> None:
            original(record)
            raise OSError("synthetic lost acknowledgment")

        with patch.object(run_admission, "_write", side_effect=write_then_fail):
            with self.assertRaises(OSError):
                interaction.answer(RUN, str(self.instance.instance_id), answer())
        raw = self.path().read_bytes()
        interaction.answer(RUN, str(self.instance.instance_id), answer())
        self.assertEqual(self.path().read_bytes(), raw)
        self.assertEqual(interaction.get(RUN)["revision"], 2)

    def test_prompt_and_advance_interruption_windows_are_retry_safe(self) -> None:
        original = run_admission._write
        for after_write in (False, True):

            def interrupted(record: dict) -> None:
                if after_write:
                    original(record)
                raise OSError("synthetic interruption")

            with patch.object(run_admission, "_write", side_effect=interrupted):
                with self.assertRaises(OSError):
                    self.put()
            self.assertEqual(interaction.get(RUN)["revision"], 1 if after_write else 0)
        raw = self.path().read_bytes()
        self.put()
        self.assertEqual(self.path().read_bytes(), raw)
        interaction.answer(RUN, str(self.instance.instance_id), answer())
        next_state = continuation(iteration=2)
        for after_write in (False, True):
            with patch.object(run_admission, "_write", side_effect=interrupted):
                with self.assertRaises(OSError):
                    interaction.advance(RUN, str(self.instance.instance_id), next_state)
            if not after_write:
                self.assertEqual(
                    interaction.get(RUN)["prompts"][0]["phase"], "answered"
                )
        raw = self.path().read_bytes()
        interaction.advance(RUN, str(self.instance.instance_id), next_state)
        self.assertEqual(self.path().read_bytes(), raw)
        self.assertEqual(interaction.get(RUN)["revision"], 3)

    def test_bulk_delete_and_concurrent_answer_cannot_restore_data(self) -> None:
        self.put()
        barrier = threading.Barrier(2)

        def submit() -> None:
            barrier.wait()
            try:
                interaction.answer(RUN, str(self.instance.instance_id), answer())
            except run_admission.AdmissionDeleted:
                pass

        def delete() -> None:
            barrier.wait()
            storage.clear_conversations()

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(submit), pool.submit(delete)]
            for job in jobs:
                job.result(timeout=10)
        self.assertEqual(run_admission.get(RUN)["phase"], "deleted")
        self.assertNotIn("interaction", run_admission.get(RUN))
        self.assertNotIn("exact text", self.path().read_text())

    def test_partial_deletion_tombstone_blocks_late_answer(self) -> None:
        self.put()
        with patch(
            "backend.storage.Path.unlink",
            side_effect=OSError("synthetic unlink failure"),
        ):
            self.assertFalse(storage.delete_conversation(f"cli-{RUN}"))
        self.assertTrue(Path(storage.get_conversation_path(f"cli-{RUN}")).exists())
        with self.assertRaises(run_admission.AdmissionDeleted):
            interaction.answer(RUN, str(self.instance.instance_id), answer())
        self.assertNotIn("interaction", run_admission.get(RUN))

    def test_restart_reads_exact_waiting_state_without_provider_or_rewrite(
        self,
    ) -> None:
        self.put()
        raw = self.path().read_bytes()
        code = "from backend import storage,run_interaction; import json,sys; storage.DATA_DIR=sys.argv[1]; print(json.dumps(run_interaction.get(sys.argv[2]),ensure_ascii=False))"
        result = subprocess.run(
            [sys.executable, "-c", code, storage.DATA_DIR, RUN],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout), interaction.get(RUN))
        self.assertEqual(self.path().read_bytes(), raw)

    def test_deletion_erases_answers_and_blocks_all_operations(self) -> None:
        self.put()
        interaction.answer(RUN, str(self.instance.instance_id), answer())
        storage.delete_conversation(f"cli-{RUN}")
        record = run_admission.get(RUN)
        self.assertEqual(
            set(record), {"version", "run_id", "conversation_id", "phase", "deleted_at"}
        )
        for operation in (
            self.put,
            lambda: interaction.get(RUN),
            lambda: interaction.answer(RUN, str(self.instance.instance_id), answer()),
            lambda: interaction.advance(
                RUN, str(self.instance.instance_id), self.saved
            ),
        ):
            with self.assertRaises(run_admission.AdmissionDeleted):
                operation()

    def test_missing_accepted_conversation_fails_closed(self) -> None:
        self.put()
        Path(storage.get_conversation_path(f"cli-{RUN}")).unlink()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            interaction.answer(RUN, str(self.instance.instance_id), answer())

    def test_corrupt_history_fails_closed_without_rewrite(self) -> None:
        self.put()
        initial = self.path().read_text()
        for corrupt in (
            lambda s: s.update(revision=2),
            lambda s: s["prompts"][0].update(phase="answered"),
            lambda s: s["prompts"][0]["instance"].update(run_id=str(uuid4())),
        ):
            record = json.loads(initial)
            corrupt(record["interaction"])
            self.path().write_text(json.dumps(record))
            raw = self.path().read_bytes()
            with self.assertRaises(run_admission.AdmissionUnavailable):
                interaction.get(RUN)
            self.assertEqual(self.path().read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
