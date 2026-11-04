"""Provider-free pre-query continuation with real receipt files and fake refinement."""

import asyncio
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

from backend import (
    durable_prequery,
    durable_refinement,
    refinement_driver,
    refinement_execution,
    run_admission,
    run_cancellation,
    run_history,
    run_inspection,
    run_interaction,
    storage,
)
from backend.consultation_contract import AnswerRequest, CreateRunRequest


class DurablePrequeryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.data = str(self.directory / "conversations")
        mocked = patch.object(storage, "DATA_DIR", self.data)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.run = self.prepare()

    def prepare(self, refine=False):
        run = str(uuid4())
        run_admission.prepare(
            run,
            CreateRunRequest(
                content="Original — вопрос",
                refine=refine,
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        return run

    def files(self):
        return {
            str(p.relative_to(self.directory)): p.read_bytes()
            for p in self.directory.rglob("*")
            if p.is_file()
        }

    def snapshot(self, run=None):
        return run_inspection.inspect(run or self.run)

    def step(self, run=None, revision=None):
        run = run or self.run
        if revision is None:
            revision = self.snapshot(run)["interaction"]["revision"]
        return durable_prequery.advance(run, expected_revision=revision)

    def answer(self, values=None, *, skipped=False, run=None):
        run = run or self.run
        instance = self.snapshot(run)["current_prompt"]["instance_id"]
        return run_interaction.answer(
            run, instance, AnswerRequest(responses=values or {}, skipped=skipped)
        )

    def rewrite(self, mutate, run=None):
        path = run_admission._path(run or self.run)
        value = json.loads(path.read_text())
        mutate(value)
        path.write_text(json.dumps(value))

    def refine(self, run, action="use_refined"):
        async def provider(**kwargs):
            return {
                "intent": "Scope",
                "clarifying_questions": [],
                "refined_prompt": "Better — вопрос",
            }

        asyncio.run(
            refinement_execution.suggest(
                run, provider, provider_key="offline-v1", expected_revision=0
            )
        )
        instance = self.snapshot(run)["current_prompt"]["instance_id"]
        self.answer({"refinement_action": action}, run=run)
        durable_refinement.resume_answer(run, instance)

    def test_unrefined_wait_and_consumption_have_separate_revisions(self):
        waiting = self.step(revision=0)
        self.assertEqual(waiting["internal_phase"], "waiting_input")
        self.assertEqual(waiting["interaction"]["revision"], 1)
        self.assertEqual(waiting["pre_query"]["user_query"], "Original — вопрос")
        before = self.files()
        self.assertEqual(self.step(revision=1), waiting)
        self.assertEqual(self.files(), before)
        with self.assertRaises(run_interaction.PromptConflict):
            self.step(revision=0)
        self.answer({"domain": "coding", "style": "concise"})
        accepted = self.snapshot()
        self.assertEqual(accepted["internal_phase"], "answer_accepted")
        done = self.step(revision=2)
        self.assertEqual(done["internal_phase"], "pre_query_complete")
        self.assertEqual(done["interaction"]["revision"], 3)
        self.assertEqual(
            done["pre_query"]["answer"],
            {"responses": {"domain": "coding", "style": "concise"}, "skipped": False},
        )
        before = self.files()
        self.assertEqual(self.step(revision=3), done)
        self.assertEqual(self.files(), before)
        self.assertEqual(done["execution"]["operations"], [])
        self.assertIsNone(done["refinement_provider"])
        self.assertIsNone(done["current_prompt"])

    def test_skip_and_empty_submission_remain_distinct_without_defaults(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run = self.prepare()
                self.step(run)
                self.answer(skipped=skipped, run=run)
                done = self.step(run)
                self.assertEqual(
                    done["pre_query"]["answer"], {"responses": {}, "skipped": skipped}
                )

    def test_refined_and_original_decisions_preserve_question_and_evidence(self):
        for action, expected in (
            ("use_refined", "Better — вопрос"),
            ("use_original", "Original — вопрос"),
        ):
            with self.subTest(action=action):
                run = self.prepare(refine=True)
                self.refine(run, action)
                previous = self.snapshot(run)
                waiting = self.step(run)
                self.assertEqual(waiting["pre_query"]["user_query"], expected)
                self.assertEqual(waiting["refinement"], previous["refinement"])
                self.assertEqual(waiting["interaction"]["revision"], 4)
                self.answer({"priority": "clarity"}, run=run)
                done = self.step(run)
                self.assertEqual(done["internal_phase"], "pre_query_complete")
                self.assertEqual(done["interaction"]["revision"], 6)
                self.assertEqual(len(done["execution"]["operations"]), 1)

    def test_cannot_skip_requested_refinement_or_unconsumed_answer(self):
        run = self.prepare(refine=True)
        before = self.files()
        with self.assertRaises(run_interaction.PromptConflict):
            self.step(run)
        self.assertEqual(self.files(), before)

        async def provider(**kwargs):
            return {
                "intent": "",
                "clarifying_questions": [],
                "refined_prompt": "Better",
            }

        asyncio.run(
            refinement_execution.suggest(
                run, provider, provider_key="offline-v1", expected_revision=0
            )
        )
        for answer in (False, True):
            if answer:
                self.answer({"refinement_action": "refine_again"}, run=run)
            before = self.files()
            with self.assertRaises(run_interaction.PromptConflict):
                self.step(run)
            self.assertEqual(self.files(), before)
        durable_refinement.resume_answer(
            run,
            self.snapshot(run)["interaction"]["prompts"][-1]["instance"]["instance_id"],
        )
        with self.assertRaises(run_interaction.PromptConflict):
            self.step(run)

    def test_current_revision_refinement_dispatch_is_rejected_but_history_is_readable(
        self,
    ):
        run = self.prepare(refine=True)
        self.refine(run)
        self.step(run)
        before = self.files()
        with self.assertRaises(refinement_execution.ExecutionConflict):
            asyncio.run(refinement_driver.advance(run, expected_revision=4))

        async def forbidden(**kwargs):
            self.fail("Historical read called provider")

        result = asyncio.run(
            refinement_execution.suggest(
                run, forbidden, provider_key="offline-v1", expected_revision=0
            )
        )
        self.assertEqual(result.phase, "waiting_answer")
        with self.assertRaises(
            (refinement_execution.ExecutionConflict, run_admission.AdmissionUnavailable)
        ):
            asyncio.run(
                refinement_execution.suggest(
                    run, forbidden, provider_key="offline-v1", expected_revision=4
                )
            )
        self.assertEqual(self.files(), before)

    def test_concurrent_preparation_and_consumption_create_one_boundary(self):
        def race(revision):
            def call(_):
                try:
                    return self.step(revision=revision)["internal_phase"]
                except run_interaction.PromptConflict:
                    return "conflict"

            with ThreadPoolExecutor(max_workers=8) as pool:
                return list(pool.map(call, range(24)))

        first = race(0)
        self.assertEqual(first.count("waiting_input"), 1)
        self.assertEqual(len(self.snapshot()["interaction"]["prompts"]), 1)
        self.answer({"style": "comprehensive"})
        second = race(2)
        self.assertEqual(second.count("pre_query_complete"), 1)
        self.assertEqual(self.snapshot()["interaction"]["revision"], 3)

    def test_storage_failures_before_and_after_replace_do_not_duplicate(self):
        original = run_admission._write
        for after in (False, True):
            for consume in (False, True):
                with self.subTest(after=after, consume=consume):
                    run = self.prepare()
                    if consume:
                        self.step(run)
                        self.answer(run=run)
                    revision = self.snapshot(run)["interaction"]["revision"]

                    def fail(record):
                        if after:
                            original(record)
                        raise OSError("synthetic write boundary")

                    with patch.object(run_admission, "_write", side_effect=fail):
                        with self.assertRaises(OSError):
                            self.step(run, revision)
                    snapshot = self.snapshot(run)
                    self.assertEqual(
                        snapshot["interaction"]["revision"], revision + int(after)
                    )
                    if after:
                        with self.assertRaises(run_interaction.PromptConflict):
                            self.step(run, revision)
                    self.step(run)
                    self.assertEqual(
                        len(self.snapshot(run)["interaction"]["prompts"]), 1
                    )

    def test_restart_restores_saved_prompt_without_regeneration_or_credentials(self):
        self.step()
        self.rewrite(
            lambda r: r["interaction"]["prompts"][-1]["instance"]["prompt"].update(
                title="Saved title — вопрос"
            )
        )
        before = self.files()
        code = """import json,sys
from backend import storage,run_inspection,durable_prequery
storage.DATA_DIR=sys.argv[1]
snapshot=run_inspection.inspect(sys.argv[2])
assert durable_prequery.advance(sys.argv[2],expected_revision=1)==snapshot
print(json.dumps(snapshot))
"""
        env = dict(
            os.environ,
            OPENROUTER_API_KEY="synthetic-unused",
            API_AUTH_KEY="test-api-key",
            PYTHON_DOTENV_DISABLED="1",
        )
        result = subprocess.run(
            [sys.executable, "-c", code, self.data, self.run],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        restored = json.loads(result.stdout)
        self.assertEqual(
            restored["current_prompt"]["prompt"]["title"], "Saved title — вопрос"
        )
        self.assertEqual(self.files(), before)

    def test_saved_labels_and_display_defaults_do_not_invent_answers(self):
        self.step()

        def modify(record):
            prompt = record["interaction"]["prompts"][-1]["instance"]["prompt"]
            prompt["title"] = "Older wording"
            prompt["fields"][0]["label"] = "Earlier domain label"
            prompt["fields"][0]["default"] = "medical"

        self.rewrite(modify)
        self.answer({})
        with patch.object(
            durable_prequery,
            "get_pre_query_prompt",
            side_effect=AssertionError("must not regenerate"),
        ):
            done = self.step()
        self.assertEqual(done["pre_query"]["answer"]["responses"], {})
        self.assertEqual(
            done["interaction"]["prompts"][0]["instance"]["prompt"]["fields"][0][
                "default"
            ],
            "medical",
        )

    def test_invalid_answers_do_not_modify_saved_prompt(self):
        self.step()
        for values in (
            {"style": "invented"},
            {"domain": True},
            {"priority": ["accuracy"]},
            {"unknown": "x"},
        ):
            before = self.files()
            with self.assertRaises(ValueError):
                self.answer(values)
            self.assertEqual(self.files(), before)

    def test_corrupt_question_continuation_schema_or_saved_answer_fail_closed(self):
        mutations = [
            lambda e: e["continuation"]["state"].update(user_query="different"),
            lambda e: e["continuation"].update(point="unknown_v1"),
            lambda e: e["continuation"]["state"].update(version=2),
            lambda e: e["instance"]["prompt"]["fields"][0].update(id="other"),
            lambda e: e["instance"]["prompt"]["fields"][0]["options"][0].update(
                value="unknown"
            ),
            lambda e: e["next_continuation"]["state"]["answer"].update(
                responses={"style": "concise"}
            ),
        ]
        for mutate in mutations:
            run = self.prepare()
            self.step(run)
            self.answer(run=run)
            self.step(run)
            self.rewrite(lambda r: mutate(r["interaction"]["prompts"][-1]), run)
            before = self.files()
            with self.assertRaises(run_admission.AdmissionUnavailable):
                self.snapshot(run)
            with self.assertRaises(run_admission.AdmissionUnavailable):
                self.step(run, 3)
            self.assertEqual(self.files(), before)

    def test_prequery_cannot_be_followed_by_another_prompt(self):
        self.step()
        self.answer()
        self.step()

        def duplicate(record):
            interaction = record["interaction"]
            entry = json.loads(json.dumps(interaction["prompts"][0]))
            entry["instance"]["instance_id"] = str(uuid4())
            interaction["prompts"].append(entry)
            interaction["revision"] = 6

        self.rewrite(duplicate)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.snapshot()

    def test_cancel_delete_and_wrong_revisions_never_create_or_resume_work(self):
        for revision in (-1, True, 1.0, "0"):
            with self.assertRaises(ValueError):
                self.step(revision=revision)
        with self.assertRaises(run_interaction.PromptConflict):
            self.step(revision=1)
        self.step()
        run_cancellation.request(self.run)
        before = self.files()
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.step()
        self.assertEqual(self.snapshot()["internal_phase"], "cancelled")
        self.assertEqual(self.files(), before)
        storage.delete_conversation(self.snapshot()["conversation_id"])
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.step(revision=1)

    def test_history_and_inspection_are_read_only_and_name_partial_completion(self):
        self.step()
        self.answer()
        self.step()
        before = self.files()
        with patch.object(
            durable_prequery,
            "get_pre_query_prompt",
            side_effect=AssertionError("no regeneration"),
        ):
            snapshot = self.snapshot()
            history = run_history.list_runs()
        self.assertEqual(snapshot["internal_phase"], "pre_query_complete")
        self.assertIn("pre_query_complete", str(history))
        self.assertEqual(self.files(), before)

    def test_manager_rejects_prequery_before_queueing_refinement(self):
        from backend.consultation_manager import ConsultationManager

        manager = object.__new__(ConsultationManager)
        manager._initialized = False
        manager.__init__()
        self.addCleanup(manager.shutdown)
        run = self.prepare(refine=True)
        self.refine(run)
        self.step(run)
        before = self.files()
        with patch.object(
            manager._executor, "submit", side_effect=AssertionError("must not queue")
        ):
            with self.assertRaises(refinement_execution.ExecutionConflict):
                manager.schedule_cli_refinement_step(run, expected_revision=4)
        self.assertEqual(self.files(), before)

    def test_missing_version_or_disconnected_refinement_prefix_fails_closed(self):
        self.step()
        self.rewrite(
            lambda record: record["interaction"]["prompts"][0]["continuation"][
                "state"
            ].pop("version")
        )
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.snapshot()
        run = self.prepare(refine=True)
        self.refine(run)
        self.step(run)

        def disconnect(record):
            entry = record["interaction"]["prompts"][0]
            entry["continuation"]["state"]["additional_context_parts"] = [
                "invented prior context"
            ]
            entry["next_continuation"]["state"]["additional_context_parts"] = [
                "invented prior context"
            ]
            record.pop("refinement_execution")

        self.rewrite(disconnect, run)
        with self.assertRaises(run_admission.AdmissionUnavailable):
            self.snapshot(run)
