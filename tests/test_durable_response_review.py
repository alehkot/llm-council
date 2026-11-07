"""Durable review from real Stage 1 receipts, with no model or network calls."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import test_run_stage1_continuation_inputs as fixtures
from test_run_stage1_inputs import LIMITS

from backend import (
    config,
    council_model_budgets,
    council_request_policy,
    council_selection,
    durable_prequery,
    durable_refinement,
    model_context,
    prequery_policy,
    refinement_execution,
    run_admission,
    run_cancellation,
    run_history,
    run_inspection,
    run_interaction,
    run_response_review,
    run_skill_plan,
    run_skill_provenance,
    run_skill_snapshot,
    run_stage1_continuation_execution,
    run_stage1_execution,
    run_stage1_inputs,
    run_stage1_results,
    storage,
)
from backend import (
    durable_response_review as review,
)
from backend.consultation_contract import AnswerRequest, CreateRunRequest

STOP = {
    "content": "  Exact answer Пример ☃\n",
    "finish_reason": "stop",
    "usage": {"test": [1]},
}


class DurableResponseReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FirstContinuationInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.initial()
        self.run = self.fixture.run_id
        self.models = self.fixture.models

    def snapshot(self, run=None):
        return run_inspection.inspect(run or self.run)

    def step(self, revision=None, run=None):
        run = run or self.run
        if revision is None:
            revision = self.snapshot(run)["interaction"]["revision"]
        return review.advance(run, expected_revision=revision)

    def complete(self, continued=False):
        if continued:
            self.fixture.execute(self.models[0])
            self.fixture.bind(self.models[0])

            async def provider(**kwargs):
                return copy.deepcopy(STOP)

            asyncio.run(
                run_stage1_continuation_execution.execute(
                    self.run,
                    self.models[0],
                    provider,
                    turn=1,
                    provider_key="test-continuation",
                    expected_revision=3,
                )
            )
        else:
            self.fixture.execute(self.models[0], STOP)
        self.fixture.execute(self.models[1], STOP)

    def answer(self, responses=None, skipped=False, run=None):
        run = run or self.run
        instance = self.snapshot(run)["interaction"]["prompts"][-1]["instance"][
            "instance_id"
        ]
        return run_interaction.answer(
            run, instance, AnswerRequest(responses=responses or {}, skipped=skipped)
        )

    def rewrite(self, mutate):
        path = run_admission._path(self.run)
        value = json.loads(path.read_text())
        mutate(value)
        path.write_text(json.dumps(value))

    def test_exact_prompt_answer_history_and_source_preservation(self):
        self.complete(continued=True)
        before = self.snapshot()
        results = run_stage1_results.load_complete_results(self.run)
        prepared = run_response_review.prepare(self.run)
        waiting = self.step(3)
        self.assertEqual(waiting["internal_phase"], "waiting_input")
        self.assertEqual(waiting["interaction"]["revision"], 4)
        self.assertEqual(
            waiting["current_prompt"]["prompt"], prepared.prompt.model_dump(mode="json")
        )
        self.assertEqual(
            waiting["response_review"]["source_sha256"], prepared.source_sha256
        )
        values = {
            "exclude_responses": list(reversed(self.models)),
            "gap_concerns": "  Не менять\n",
        }
        self.answer(values)
        self.assertEqual(self.snapshot()["internal_phase"], "answer_accepted")
        done = self.step(5)
        self.assertEqual(done["internal_phase"], "response_review_complete")
        self.assertEqual(done["interaction"]["revision"], 6)
        self.assertIsNone(done["current_prompt"])
        self.assertEqual(
            done["response_review"]["answer"], {"responses": values, "skipped": False}
        )
        for name in (
            "stage1",
            "stage1_inputs",
            "stage1_execution",
            "stage1_continuation_inputs",
            "stage1_continuation_execution",
            "pre_query_policy",
            "council_selection",
            "council_request_policy",
            "council_model_budgets",
            "skill_plan",
        ):
            self.assertEqual(done[name], before[name], name)
        files = self.fixture.files()
        self.assertEqual(run_stage1_results.load_complete_results(self.run), results)
        self.assertEqual(run_response_review.prepare(self.run), prepared)
        self.assertEqual(self.step(6), done)
        self.assertIn("response_review_complete", str(run_history.list_runs()))
        self.assertEqual(self.fixture.files(), files)

    def test_waiting_and_answered_omissions_skip_and_defaults_remain_distinct(self):
        self.complete()
        self.step()
        self.rewrite(
            lambda r: r["interaction"]["prompts"][-1]["instance"]["prompt"]["fields"][
                0
            ].update(default=[self.models[0]])
        )
        self.answer({})
        done = self.step()
        self.assertEqual(
            done["response_review"]["answer"], {"responses": {}, "skipped": False}
        )
        self.assertEqual(
            done["interaction"]["prompts"][-1]["instance"]["prompt"]["fields"][0][
                "default"
            ],
            [self.models[0]],
        )

    def test_explicit_skip_is_preserved_without_effective_exclusions(self):
        self.complete()
        self.step()
        self.answer(skipped=True)
        self.assertEqual(
            self.step()["response_review"]["answer"], {"responses": {}, "skipped": True}
        )

    def test_first_answer_wins_and_invalid_values_do_not_write(self):
        self.complete()
        self.step()
        for value in (
            {"exclude_responses": ["unknown"]},
            {"exclude_responses": [self.models[0]] * 2},
            {"gap_concerns": True},
            {"unknown": "x"},
        ):
            before = self.fixture.files()
            with self.assertRaises(ValueError):
                self.answer(value)
            self.assertEqual(before, self.fixture.files())
        self.answer({"gap_concerns": "first"})
        before = self.fixture.files()
        self.answer({"gap_concerns": "first"})
        with self.assertRaises(run_interaction.PromptConflict):
            self.answer({"gap_concerns": "second"})
        self.assertEqual(before, self.fixture.files())

    def test_restart_keeps_exact_instance_schema_without_regeneration(self):
        self.complete()
        self.step()
        self.rewrite(
            lambda r: r["interaction"]["prompts"][-1]["instance"]["prompt"].update(
                title="Saved wording ☃"
            )
        )
        before = self.fixture.files()
        code = """import json,sys
from unittest.mock import patch
from backend import storage,run_inspection,run_response_review,durable_response_review
storage.DATA_DIR=sys.argv[1]
with patch.object(run_response_review,'get_response_review_prompt',side_effect=AssertionError('no regeneration')):
 s=run_inspection.inspect(sys.argv[2])
 assert durable_response_review.advance(sys.argv[2],expected_revision=4)==s
 print(json.dumps(s))
"""
        result = subprocess.run(
            [sys.executable, "-c", code, storage.DATA_DIR, self.run],
            env={**os.environ, "PYTHON_DOTENV_DISABLED": "1"},
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        restored = json.loads(result.stdout)
        self.assertEqual(
            restored["current_prompt"]["prompt"]["title"], "Saved wording ☃"
        )
        self.assertEqual(before, self.fixture.files())

    def test_incomplete_uncertain_and_invalid_revision_cannot_create_review(self):
        for revision in (-1, True, 3.0, "3"):
            with self.assertRaises(ValueError):
                self.step(revision)
        for complete_one in (False, True):
            if complete_one:
                self.fixture.execute(self.models[0], STOP)
            before = self.fixture.files()
            with self.assertRaises(run_interaction.PromptConflict):
                self.step(3)
            self.assertEqual(before, self.fixture.files())

        async def fail(**kwargs):
            raise RuntimeError("synthetic uncertainty")

        with self.assertRaises(run_stage1_execution.ProviderFailure):
            asyncio.run(
                run_stage1_execution.execute(
                    self.run,
                    self.models[1],
                    fail,
                    provider_key="synthetic-v1",
                    expected_revision=3,
                )
            )
        with self.assertRaises(run_interaction.PromptConflict):
            self.step(3)

    def test_old_stage_dispatch_is_closed_after_review_without_calls(self):
        self.complete(continued=True)
        self.step()

        async def forbidden(**kwargs):
            self.fail("provider must not execute")

        before = self.fixture.files()
        for revision in (3, 4):
            with self.assertRaises(run_stage1_execution.ExecutionConflict):
                asyncio.run(
                    run_stage1_execution.execute(
                        self.run,
                        self.models[0],
                        forbidden,
                        provider_key="synthetic-v1",
                        expected_revision=revision,
                    )
                )
            with self.assertRaises(run_stage1_continuation_execution.ExecutionConflict):
                asyncio.run(
                    run_stage1_continuation_execution.execute(
                        self.run,
                        self.models[0],
                        forbidden,
                        turn=1,
                        provider_key="test-continuation",
                        expected_revision=revision,
                    )
                )
        with self.assertRaises(run_interaction.PromptConflict):
            durable_prequery.advance(self.run, expected_revision=4)
        self.assertEqual(before, self.fixture.files())

    def test_concurrent_creation_answer_consumption_keep_one_boundary(self):
        self.complete()

        def attempt(revision):
            try:
                return self.step(revision)["internal_phase"]
            except run_interaction.PromptConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: attempt(3), range(24)))
        self.assertEqual(results.count("waiting_input"), 1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.answer({"gap_concerns": "same"}), range(24)))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: attempt(5), range(24)))
        self.assertEqual(results.count("response_review_complete"), 1)
        self.assertEqual(len(self.snapshot()["interaction"]["prompts"]), 2)

    def test_cancelled_evidence_readable_but_mutations_and_deletion_fail(self):
        self.complete()
        waiting = self.step()
        run_cancellation.request(self.run)
        before = self.fixture.files()
        cancelled = self.snapshot()
        self.assertEqual(cancelled["internal_phase"], "cancelled")
        self.assertEqual(cancelled["response_review"], waiting["response_review"])
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.step(4)
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.answer()
        self.assertEqual(before, self.fixture.files())
        storage.delete_conversation(cancelled["conversation_id"])
        self.assertNotIn("gap_concerns", run_admission._path(self.run).read_text())
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.step(4)

    def test_failed_and_lost_write_acknowledgments_do_not_duplicate(self):
        self.complete()
        original = run_admission._write
        for consume, revision in ((False, 3), (True, 5)):
            if consume:
                self.answer()
            for after in (False, True):

                def fail(record):
                    if after:
                        original(record)
                    raise OSError("synthetic lost acknowledgment")

                with patch.object(run_admission, "_write", side_effect=fail):
                    with self.assertRaises(OSError):
                        self.step(revision)
                self.assertEqual(
                    self.snapshot()["interaction"]["revision"], revision + int(after)
                )
            with self.assertRaises(run_interaction.PromptConflict):
                self.step(revision)
            self.step()
        self.assertEqual(len(self.snapshot()["interaction"]["prompts"]), 2)

    def test_corrupt_source_schema_or_derivation_fails_closed(self):
        self.complete()
        self.step()
        self.answer()
        self.step()
        original = run_admission._path(self.run).read_bytes()
        mutations = [
            lambda r: r["stage1_execution"]["operations"][0]["result"].update(
                content="altered"
            ),
            lambda r: r["stage1_execution"]["operations"][0].update(
                attempt=str(uuid4())
            ),
            lambda r: r["interaction"]["prompts"][-1]["continuation"]["state"].update(
                source_revision=6
            ),
            lambda r: r["interaction"]["prompts"][-1]["continuation"]["state"].update(
                source_sha256="0" * 64
            ),
            lambda r: r["interaction"]["prompts"][-1]["instance"]["prompt"]["fields"][
                0
            ]["options"][0].update(value="other"),
            lambda r: r["interaction"]["prompts"][-1]["next_continuation"]["state"][
                "answer"
            ].update(responses={"gap_concerns": "invented"}),
            lambda r: r["pre_query_policy"].update(source_revision=6),
        ]
        for mutate in mutations:
            run_admission._path(self.run).write_bytes(original)
            self.rewrite(mutate)
            before = self.fixture.files()
            with self.assertRaises(run_admission.AdmissionUnavailable):
                self.snapshot()
            self.assertEqual(before, self.fixture.files())

    def test_duplicate_review_or_unrecognized_tail_is_rejected(self):
        self.complete()
        self.step()
        self.answer()
        self.step()
        original = run_admission._path(self.run).read_bytes()
        for point in (review.POINT, durable_prequery.POINT, "unknown"):
            run_admission._path(self.run).write_bytes(original)

            def mutate(record):
                entry = copy.deepcopy(record["interaction"]["prompts"][-1])
                entry["instance"]["instance_id"] = str(uuid4())
                entry["continuation"]["point"] = point
                record["interaction"]["prompts"].append(entry)
                record["interaction"]["revision"] += 3

            self.rewrite(mutate)
            with self.assertRaises(run_admission.AdmissionUnavailable):
                self.snapshot()

    def test_refined_prefix_retains_causal_revisions_through_review(self):
        for rounds in (1, 2):
            run = str(uuid4())
            run_admission.prepare(
                run,
                CreateRunRequest(
                    content="Original",
                    refine=True,
                    skill_ids=["one"],
                    interaction_policy="persistent",
                    recovery_policy="explicit_retry",
                ),
            )
            run_skill_provenance.bind_explicit_skills(run)
            run_skill_snapshot.persist(run, expected_revision=0)

            async def suggest(**kwargs):
                return {
                    "intent": "scope",
                    "clarifying_questions": [],
                    "refined_prompt": "Refined ☃",
                }

            for index in range(rounds):
                asyncio.run(
                    refinement_execution.suggest(
                        run, suggest, provider_key="test", expected_revision=index * 3
                    )
                )
                instance = self.snapshot(run)["current_prompt"]["instance_id"]
                run_interaction.answer(
                    run,
                    instance,
                    AnswerRequest(
                        responses={
                            "refinement_action": "use_refined"
                            if index == rounds - 1
                            else "refine_again"
                        }
                    ),
                )
                durable_refinement.resume_answer(run, instance)
            start = rounds * 3
            prompt = durable_prequery.advance(run, expected_revision=start)[
                "current_prompt"
            ]
            run_interaction.answer(
                run, prompt["instance_id"], AnswerRequest(responses={})
            )
            durable_prequery.advance(run, expected_revision=start + 2)
            revision = start + 3
            prequery_policy.bind(run, expected_revision=revision)
            run_skill_plan.bind(run, expected_revision=revision)
            council_selection.bind(run, expected_revision=revision)
            council_request_policy.bind(run, expected_revision=revision)
            with (
                patch.object(
                    model_context, "OPENROUTER_API_URL", config.OPENROUTER_API_URL
                ),
                patch.object(
                    model_context, "model_limits", AsyncMock(return_value=LIMITS)
                ),
            ):
                asyncio.run(council_model_budgets.bind(run, expected_revision=revision))
            run_stage1_inputs.bind(run, expected_revision=revision)

            async def provider(**kwargs):
                return STOP

            for model in self.models:
                asyncio.run(
                    run_stage1_execution.execute(
                        run,
                        model,
                        provider,
                        provider_key="test",
                        expected_revision=revision,
                    )
                )
            before = self.snapshot(run)
            self.step(revision, run)
            self.answer(run=run)
            done = self.step(revision + 2, run)
            self.assertEqual(done["internal_phase"], "response_review_complete")
            self.assertEqual(done["pre_query_policy"]["source_revision"], revision)
            self.assertEqual(done["stage1_inputs"], before["stage1_inputs"])
            self.assertEqual(done["refinement"], before["refinement"])
            self.assertEqual(done["execution"], before["execution"])
