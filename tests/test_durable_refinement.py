"""Offline receipt-to-refinement integration; no providers or live credentials."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import durable_refinement, run_admission, run_interaction, storage
from backend.consultation_contract import (
    AnswerRequest,
    CreateRunRequest,
    PromptInstance,
)
from backend.interactive import get_prompt_refinement_prompt
from backend.refinement import RefinementState

ORIGINAL = "  Original question — исходный вопрос  "
SUGGESTION = {
    "intent": "Saved intent",
    "clarifying_questions": ["", "Which scope?"],
    "refined_prompt": "  Saved refined question  ",
}


class DurableRefinementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_dir = str(Path(self.directory.name) / "conversations")
        self.patch = patch.object(storage, "DATA_DIR", self.data_dir)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.run_id = str(uuid4())
        run_admission.prepare(
            self.run_id,
            CreateRunRequest(
                content=ORIGINAL,
                refine=True,
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        self.state = RefinementState.start(ORIGINAL).accept_suggestion(SUGGESTION)
        self.instance = PromptInstance(
            run_id=self.run_id,
            instance_id=uuid4(),
            prompt=get_prompt_refinement_prompt(SUGGESTION).to_dict(),
        )
        run_interaction.put_prompt(
            self.run_id,
            self.instance,
            self.state.to_continuation(),
            expected_revision=0,
        )
        self.path = Path(self.directory.name) / "run_receipts" / f"{self.run_id}.json"

    def accept(self, **values):
        answer = AnswerRequest(responses={"refinement_action": "use_refined", **values})
        run_interaction.answer(self.run_id, str(self.instance.instance_id), answer)
        return answer

    def resume(self):
        return durable_refinement.resume_answer(
            self.run_id, str(self.instance.instance_id)
        )

    def rewrite(self, mutate):
        record = json.loads(self.path.read_text())
        mutate(record)
        self.path.write_text(json.dumps(record))

    def test_consumes_saved_answer_preserves_raw_text_and_duplicate_is_read(self):
        answer = self.accept(refined_prompt="  exact edited\ntext  ")
        result = self.resume()
        self.assertEqual(result.refined_prompt, "exact edited\ntext")
        self.assertEqual(result.phase, "complete")
        saved = run_interaction.get(self.run_id)
        self.assertEqual(saved["revision"], 3)
        self.assertEqual(saved["prompts"][0]["answer"], answer.model_dump(mode="json"))
        before = self.path.read_bytes()
        with patch.object(run_admission, "_write", side_effect=AssertionError("write")):
            self.assertEqual(self.resume(), result)
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_consumers_apply_answer_once(self):
        self.accept(additional_insights="  one saved detail  ")
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(lambda _: self.resume(), range(16)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(results[0].phase, "needs_suggestion")
        self.assertEqual(results[0].iteration, 2)
        self.assertEqual(results[0].additional_context_parts, ["one saved detail"])
        self.assertEqual(run_interaction.get(self.run_id)["revision"], 3)

    def test_fresh_process_resumes_without_network_or_provider_import(self):
        self.accept(refined_prompt="  saved restart answer  ")
        program = """
import json, socket, sys
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
from backend import storage, durable_refinement
storage.DATA_DIR = sys.argv[1]
result = durable_refinement.resume_answer(sys.argv[2], sys.argv[3])
assert 'backend.council' not in sys.modules
assert 'backend.langgraph_flow' not in sys.modules
print(result.model_dump_json())
"""
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                self.data_dir,
                self.run_id,
                str(self.instance.instance_id),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        result = RefinementState.model_validate_json(completed.stdout)
        self.assertEqual(result.refined_prompt, "saved restart answer")
        self.assertEqual(self.resume(), result)

    def test_write_failure_keeps_accepted_answer_and_can_retry_without_provider(self):
        self.accept(additional_insights="retained")
        before = self.path.read_bytes()
        with patch.object(
            run_admission, "_write", side_effect=OSError("synthetic write failure")
        ):
            with self.assertRaises(OSError):
                self.resume()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(
            run_interaction.get(self.run_id)["prompts"][0]["phase"], "answered"
        )
        self.assertEqual(self.resume().additional_context_parts, ["retained"])

    def test_consumed_answer_replay_does_not_touch_later_waiting_prompt(self):
        self.accept(refinement_action="refine_again")
        first = self.resume()
        later_state = first.accept_suggestion(SUGGESTION)
        later = PromptInstance(
            run_id=self.run_id,
            instance_id=uuid4(),
            prompt=get_prompt_refinement_prompt(SUGGESTION, iteration=2).to_dict(),
        )
        run_interaction.put_prompt(
            self.run_id,
            later,
            later_state.to_continuation(),
            expected_revision=3,
        )
        before = self.path.read_bytes()
        self.assertEqual(self.resume(), first)
        self.assertEqual(self.path.read_bytes(), before)
        saved = run_interaction.get(self.run_id)
        self.assertEqual(saved["revision"], 4)
        self.assertEqual(saved["prompts"][-1]["phase"], "waiting")

    def test_lost_write_acknowledgment_replays_saved_result_without_rewrite(self):
        self.accept(additional_insights="persisted once")
        original_write = run_admission._write

        def write_then_fail(record):
            original_write(record)
            raise OSError("synthetic lost acknowledgment")

        with patch.object(run_admission, "_write", side_effect=write_then_fail):
            with self.assertRaises(OSError):
                self.resume()
        before = self.path.read_bytes()
        with patch.object(
            run_admission, "_write", side_effect=AssertionError("rewrite")
        ):
            result = self.resume()
        self.assertEqual(result.additional_context_parts, ["persisted once"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_waiting_unknown_and_deleted_fail_without_writes(self):
        before = self.path.read_bytes()
        with self.assertRaises(run_interaction.PromptConflict):
            self.resume()
        with self.assertRaises(run_interaction.PromptConflict):
            durable_refinement.resume_answer(self.run_id, str(uuid4()))
        self.assertEqual(self.path.read_bytes(), before)
        run_admission.tombstone_conversation(f"cli-{self.run_id}")
        tombstone = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.resume()
        self.assertEqual(self.path.read_bytes(), tombstone)

    def test_incompatible_saved_schema_is_not_reinterpreted(self):
        self.accept()
        self.rewrite(
            lambda record: record["interaction"]["prompts"][0]["instance"][
                "prompt"
            ].update(prompt_id="different_prompt")
        )
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.resume()
        self.assertEqual(self.path.read_bytes(), before)

    def test_saved_ui_copy_and_defaults_are_not_regenerated_or_applied(self):
        self.accept()

        def change_copy(record):
            prompt = record["interaction"]["prompts"][0]["instance"]["prompt"]
            prompt.update(title="Earlier localized title", description="Saved wording")
            prompt["fields"][0]["label"] = "Earlier action label"
            prompt["fields"][1]["default"] = "A different saved display default"

        self.rewrite(change_copy)
        result = self.resume()
        self.assertEqual(result.refined_prompt, "Saved refined question")
        prompt = run_interaction.get(self.run_id)["prompts"][0]["instance"]["prompt"]
        self.assertEqual(prompt["title"], "Earlier localized title")
        self.assertEqual(
            prompt["fields"][1]["default"], "A different saved display default"
        )

    def test_wrong_clarification_question_fails_without_write(self):
        self.accept(clarifying_answer_1="one answer")

        def change_question(record):
            record["interaction"]["prompts"][0]["instance"]["prompt"]["fields"][2][
                "label"
            ] = "A different question?"

        self.rewrite(change_question)
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.resume()
        self.assertEqual(self.path.read_bytes(), before)

    def test_changed_action_or_field_semantics_cannot_consume_answer(self):
        self.accept()
        original = self.path.read_bytes()

        def missing_field(prompt):
            prompt["fields"] = [
                field for field in prompt["fields"] if field["id"] != "refined_prompt"
            ]

        def optional_action(prompt):
            prompt["fields"][0]["required"] = False

        def new_action(prompt):
            prompt["fields"][0]["options"].append(
                {"value": "surprise", "label": "New semantics"}
            )

        def wrong_type(prompt):
            prompt["fields"][1].update(type="checkbox", default=False)

        for mutate in [missing_field, optional_action, new_action, wrong_type]:
            self.path.write_bytes(original)
            self.rewrite(
                lambda record: mutate(
                    record["interaction"]["prompts"][0]["instance"]["prompt"]
                )
            )
            before = self.path.read_bytes()
            with self.assertRaises(run_admission.AdmissionUnavailable):
                self.resume()
            self.assertEqual(self.path.read_bytes(), before)

    def test_wrong_continuation_or_admission_fails_closed(self):
        self.accept()
        original = self.path.read_bytes()
        mutations = [
            lambda record: record["interaction"]["prompts"][0]["continuation"].update(
                point="different_phase"
            ),
            lambda record: record["interaction"]["prompts"][0]["continuation"][
                "state"
            ].update(original_query="other question"),
            lambda record: record["request"].update(refine=False),
        ]
        for mutate in mutations:
            self.path.write_bytes(original)
            self.rewrite(mutate)
            before = self.path.read_bytes()
            with self.assertRaises(run_admission.AdmissionUnavailable):
                self.resume()
            self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_consumed_result_is_detected_not_repaired(self):
        self.accept()
        self.resume()
        self.rewrite(
            lambda record: record["interaction"]["prompts"][0]["next_continuation"][
                "state"
            ].update(refined_prompt="wrong result")
        )
        before = self.path.read_bytes()
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.resume()
        self.assertEqual(self.path.read_bytes(), before)

    def test_saved_skip_uses_original_without_timeout_or_default_answer(self):
        run_interaction.answer(
            self.run_id, str(self.instance.instance_id), AnswerRequest(skipped=True)
        )
        result = self.resume()
        self.assertEqual(result.phase, "complete")
        self.assertEqual(result.refined_prompt, ORIGINAL)
        self.assertEqual(result.decision, "use_original")


if __name__ == "__main__":
    unittest.main()
