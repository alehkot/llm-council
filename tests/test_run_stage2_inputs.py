"""Offline exact ranking preparation from real saved Stage 1/review receipts."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import test_response_review_policy as fixtures
from test_durable_response_review import STOP
from test_run_stage1_inputs import LIMITS

from backend import (
    config,
    council,
    council_model_budgets,
    council_request_policy,
    council_selection,
    durable_prequery,
    durable_refinement,
    durable_response_review,
    langgraph_flow,
    model_context,
    prequery_policy,
    refinement_execution,
    response_review_policy,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    run_response_review,
    run_skill_plan,
    run_skill_provenance,
    run_skill_snapshot,
    run_stage1_execution,
    run_stage1_inputs,
    run_stage1_results,
    storage,
)
from backend import run_stage2_inputs as stage2
from backend.consultation_contract import AnswerRequest, CreateRunRequest
from backend.interactive import InteractiveConfig, PromptResponse


class Stage2InputTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResponseReviewPolicyTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def complete(self, responses=None, **kwargs):
        self.fixture.complete(responses, **kwargs)
        self.run, self.models = self.fixture.run, self.fixture.models
        self.policy = self.fixture.bind()
        self.revision = 6

    def bind(self, revision=None):
        return stage2.bind(
            self.run, expected_revision=self.revision if revision is None else revision
        )

    def files(self):
        return self.fixture.files()

    def rewrite(self, mutate):
        path = run_admission._path(self.run)
        record = json.loads(path.read_text())
        mutate(record)
        path.write_text(json.dumps(record))

    def test_exact_requests_full_reviewer_roster_and_source_identities(self):
        self.complete({"exclude_responses": ["provider/first"]})
        before = run_inspection.inspect(self.run)
        saved = self.bind()
        self.assertEqual(saved.active_models, (self.models[1],))
        self.assertEqual(saved.label_to_model, {"Response A": self.models[1]})
        self.assertEqual(tuple(r.model for r in saved.requests), tuple(self.models))
        self.assertEqual(saved.ranking_method, "average_rank")
        self.assertEqual(saved.source_revision, 6)
        record, interaction = run_interaction._load(self.run)
        for name, value in (
            ("response_policy", prequery_policy.validate_saved(record, interaction)),
            ("review_policy", self.policy),
            ("selection", council_selection.validate_saved(record, interaction)),
            (
                "model_budgets",
                council_model_budgets.validate_saved(record, interaction),
            ),
            (
                "request_policy",
                council_request_policy.validate_saved(record, interaction),
            ),
        ):
            self.assertEqual(
                getattr(saved, name + "_sha256"), run_stage1_inputs._digest(value)
            )
        self.assertEqual(saved.stage1_source_sha256, self.policy.stage1_source_sha256)
        messages = [m.model_dump(mode="json") for m in saved.messages]
        self.assertEqual(saved.input_token_bound, model_context.token_bound(messages))
        self.assertIn("Response A:\n" + STOP["content"], messages[0]["content"])
        for index, request in enumerate(saved.requests):
            expected = {
                "model": self.models[index],
                "messages": messages,
                "max_tokens": 1234,
            }
            if index == 0:
                expected["reasoning"] = {"effort": "high"}
            self.assertEqual(request.payload(), expected)
        after = run_inspection.inspect(self.run)
        for key in (
            "interaction",
            "internal_phase",
            "stage1",
            "response_review",
            "response_review_policy",
        ):
            self.assertEqual(before[key], after[key])
        self.assertNotEqual(before["cursor"], after["cursor"])
        self.assertEqual(after["stage2_inputs"], stage2.summary(saved))
        self.assertNotIn("Exact answer", json.dumps(after["stage2_inputs"]))
        self.assertNotIn("Exact answer", repr(saved))

    def assert_legacy_parity(self, saved, *, refine=False, skipped=False):
        record, interaction = run_interaction._load(self.run)
        response = prequery_policy.validate_saved(record, interaction)
        results = run_stage1_results.load_complete_results(self.run)
        active = [
            {"model": r.model, "response": r.content}
            for r in results
            if r.model in saved.active_models
        ]
        answer = PromptResponse(
            prompt_id="pre_query_config", responses={}, skipped=skipped
        )
        context = SimpleNamespace(
            prompt_refinement_enabled=refine,
            collected_responses={"pre_query_config": answer},
            interactive_config=InteractiveConfig.from_responses(
                {"pre_query_config": answer}
            ),
        )
        modifier = langgraph_flow._stage2_prompt_modifier(context)
        calls = []

        async def query(model, messages, **kwargs):
            calls.append((model, copy.deepcopy(messages)))
            return {"content": "FINAL RANKING:\n1. Response A\n2. Response B"}

        with (
            patch.object(council, "COUNCIL_MODELS", self.models),
            patch.object(council, "query_model", query),
        ):
            asyncio.run(
                council.stage2_collect_rankings(
                    response.user_query,
                    active,
                    prompt_modifier=modifier,
                )
            )
        self.assertEqual([m for m, _ in calls], self.models)
        self.assertTrue(
            all(
                messages == [m.model_dump(mode="json") for m in saved.messages]
                for _, messages in calls
            )
        )

    def test_real_legacy_ranking_path_has_identical_messages(self):
        self.complete({"exclude_responses": ["provider/first"]})
        self.assert_legacy_parity(self.bind())

    def test_negative_path_and_all_excluded_are_blocked_without_writes(self):
        for values, kwargs in (
            ({}, {"negative": True}),
            ({}, {"negative": True, "requested": "Exact concern"}),
            ({"exclude_responses": ["provider/first", "provider/second"]}, {}),
        ):
            self.complete(values, **kwargs)
            before = self.files()
            with self.assertRaises(stage2.Stage2InputConflict):
                self.bind()
            self.assertEqual(self.files(), before)

    def test_unfinished_missing_policy_and_invalid_revisions_rejected(self):
        self.fixture.prepare()
        self.run, self.models, self.revision = self.fixture.run, self.fixture.models, 3
        for revision in (True, -1, "3"):
            with self.assertRaises(ValueError):
                self.bind(revision)
        before = self.files()
        with self.assertRaises(stage2.Stage2InputConflict):
            self.bind()
        self.assertEqual(self.files(), before)
        self.complete()
        before = self.files()
        with self.assertRaises(run_interaction.PromptConflict):
            self.bind(5)
        self.assertEqual(self.files(), before)
        self.rewrite(lambda r: r.pop("response_review_policy"))
        before = self.files()
        with self.assertRaises(stage2.Stage2InputConflict):
            self.bind()
        self.assertEqual(self.files(), before)

    def test_duplicate_concurrent_binds_and_detached_results(self):
        self.complete()
        with patch.object(
            run_admission, "_write", wraps=run_admission._write
        ) as writer:
            with ThreadPoolExecutor(max_workers=6) as pool:
                values = list(pool.map(lambda _: self.bind(), range(18)))
            self.assertEqual(writer.call_count, 1)
        before = self.files()
        values[0].label_to_model.clear()
        values[0].requests[0].payload()["messages"].clear()
        self.assertEqual(self.bind(), values[1])
        self.assertEqual(self.files(), before)

    def test_current_config_and_prompt_factory_are_not_historical_sources(self):
        self.complete()
        saved = self.bind()
        with (
            patch.object(config, "COUNCIL_MODELS", []),
            patch.object(config, "NEGATIVE_MODE_ENABLED", True),
            patch.object(config, "OPENROUTER_MAX_TOKENS", 1),
            patch.object(
                run_response_review,
                "get_response_review_prompt",
                side_effect=AssertionError("Do not recreate prompt"),
            ),
        ):
            self.assertEqual(self.bind(), saved)
            self.assertEqual(
                run_inspection.inspect(self.run)["stage2_inputs"], stage2.summary(saved)
            )

        def reorder(r):
            r["response_review_policy"]["label_to_model"] = dict(
                reversed(list(r["response_review_policy"]["label_to_model"].items()))
            )
            r["stage2_inputs"]["label_to_model"] = dict(
                reversed(list(r["stage2_inputs"]["label_to_model"].items()))
            )

        self.rewrite(reorder)
        self.assertEqual(self.bind(), saved)

    def test_real_oversized_answers_require_guarded_reduction(self):
        self.fixture.prepare()
        self.run, self.models, self.revision = self.fixture.run, self.fixture.models, 6
        for model in self.models:
            self.fixture.fixture.execute(model, {**STOP, "content": "large " * 30000})
        durable_response_review.advance(self.run, expected_revision=3)
        self.fixture.answer()
        durable_response_review.advance(self.run, expected_revision=5)
        self.fixture.bind()
        before = self.files()
        with self.assertRaisesRegex(stage2.Stage2InputConflict, "context reduction"):
            self.bind()
        self.assertEqual(self.files(), before)

    def test_exact_budget_boundary_and_one_token_overflow(self):
        self.complete()
        record, interaction = run_interaction._load(self.run)
        budgets = council_model_budgets.validate_saved(record, interaction)
        minimum = min(b.input_budget for b in budgets.models)
        original = model_context.token_bound

        # Alter only Stage 2 prompt counting; historical Stage 1 stays intact.
        def count(messages):
            return (
                minimum
                if messages[0]["content"].startswith("You are evaluating")
                else original(messages)
            )

        with patch.object(model_context, "token_bound", side_effect=count):
            self.assertEqual(
                stage2._derive(record, interaction).input_token_bound, minimum
            )

        def over(messages):
            return (
                minimum + 1
                if messages[0]["content"].startswith("You are evaluating")
                else original(messages)
            )

        before = self.files()
        with patch.object(model_context, "token_bound", side_effect=over):
            with self.assertRaises(stage2.Stage2InputConflict):
                self.bind()
        self.assertEqual(self.files(), before)

    def test_tampered_receipts_and_sources_fail_closed(self):
        self.complete({"exclude_responses": ["provider/first"]})
        self.bind()
        path = run_admission._path(self.run)
        original = path.read_bytes()
        mutations = [
            lambda r: r["stage2_inputs"].update(version=True),
            lambda r: r["stage2_inputs"].update(source_revision=3),
            lambda r: r["stage2_inputs"].update(stage1_source_sha256="0" * 64),
            lambda r: r["stage2_inputs"].update(label_to_model={}),
            lambda r: r["stage2_inputs"]["messages"][0].update(content="changed"),
            lambda r: r["stage2_inputs"]["requests"][0].update(payload_json="{}"),
            lambda r: r["stage2_inputs"].update(ranking_method="pairwise"),
            lambda r: r["stage2_inputs"].update(endpoint="https://other.invalid"),
            lambda r: r["stage2_inputs"].update(unknown=True),
            lambda r: r["stage1_execution"]["operations"][0]["result"].update(
                content="excluded but changed"
            ),
            lambda r: r.pop("response_review_policy"),
            lambda r: r.update(phase="initializing"),
        ]
        for mutate in mutations:
            path.write_bytes(original)
            self.rewrite(mutate)
            before = self.files()
            with self.assertRaises(run_admission.AdmissionUnavailable):
                run_inspection.inspect(self.run)
            with self.assertRaises(run_admission.AdmissionUnavailable):
                self.bind()
            self.assertEqual(self.files(), before)

    def test_failed_write_and_lost_acknowledgment(self):
        self.complete()
        before = self.files()
        with patch.object(run_admission, "_write", side_effect=OSError("before write")):
            with self.assertRaises(OSError):
                self.bind()
        self.assertEqual(self.files(), before)
        writer = run_admission._write

        def lost(record):
            writer(record)
            raise OSError("lost acknowledgment")

        with patch.object(run_admission, "_write", side_effect=lost):
            with self.assertRaises(OSError):
                self.bind()
        before = self.files()
        with patch.object(
            run_admission, "_write", side_effect=AssertionError("duplicate write")
        ):
            self.bind()
        self.assertEqual(self.files(), before)

    def test_cancellation_retains_readable_receipt_deletion_erases_it(self):
        self.complete()
        saved = self.bind()
        run_cancellation.request(self.run)
        snapshot = run_inspection.inspect(self.run)
        self.assertEqual(snapshot["internal_phase"], "cancelled")
        self.assertEqual(snapshot["stage2_inputs"], stage2.summary(saved))
        before = self.files()
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()
        self.assertEqual(self.files(), before)
        storage.delete_conversation(snapshot["conversation_id"])
        self.assertNotIn("stage2_inputs", run_admission._path(self.run).read_text())
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.bind()

    def test_fresh_process_restores_exact_request_without_live_settings(self):
        self.complete()
        saved = self.bind().model_dump(mode="json")
        before = self.files()
        script = """import json,sys
from unittest.mock import patch
from backend import config,storage,run_stage2_inputs,run_response_review
storage.DATA_DIR=sys.argv[1]
config.COUNCIL_MODELS=[]
config.NEGATIVE_MODE_ENABLED=True
with patch.object(run_response_review,'get_response_review_prompt',side_effect=AssertionError('no regeneration')):
 print(json.dumps(run_stage2_inputs.bind(sys.argv[2],expected_revision=6).model_dump(mode='json')))
"""
        result = subprocess.check_output(
            [sys.executable, "-c", script, storage.DATA_DIR, self.run],
            timeout=20,
            text=True,
            env={
                **os.environ,
                "OPENROUTER_API_KEY": "test-only",
                "API_AUTH_KEY": "test-only",
            },
        )
        self.assertEqual(json.loads(result), saved)
        self.assertEqual(self.files(), before)

    def test_refinement_and_prequery_skip_match_legacy_guidance_and_saved_query(self):
        self.complete()  # Reuse only the synthetic storage/settings fixture.
        setting = patch.object(config, "PAIRWISE_RANKING_ENABLED", True)
        setting.start()
        self.addCleanup(setting.stop)
        for skipped_refinement in (False, True):
            for skipped_prequery in (False, True):
                self.run = str(uuid4())
                run_admission.prepare(
                    self.run,
                    CreateRunRequest(
                        content="Original exact ☃",
                        refine=True,
                        skill_ids=["one"],
                        interaction_policy="persistent",
                        recovery_policy="explicit_retry",
                    ),
                )
                run_skill_provenance.bind_explicit_skills(self.run)
                run_skill_snapshot.persist(self.run, expected_revision=0)

                async def suggest(**kwargs):
                    return {
                        "intent": "scope",
                        "clarifying_questions": [],
                        "refined_prompt": " Refined exact Пример\n",
                    }

                asyncio.run(
                    refinement_execution.suggest(
                        self.run, suggest, provider_key="test", expected_revision=0
                    )
                )
                instance = run_inspection.inspect(self.run)["current_prompt"][
                    "instance_id"
                ]
                run_interaction.answer(
                    self.run,
                    instance,
                    AnswerRequest(
                        skipped=skipped_refinement,
                        responses={}
                        if skipped_refinement
                        else {"refinement_action": "use_refined"},
                    ),
                )
                durable_refinement.resume_answer(self.run, instance)
                prompt = durable_prequery.advance(self.run, expected_revision=3)[
                    "current_prompt"
                ]
                run_interaction.answer(
                    self.run,
                    prompt["instance_id"],
                    AnswerRequest(responses={}, skipped=skipped_prequery),
                )
                durable_prequery.advance(self.run, expected_revision=5)
                prequery_policy.bind(self.run, expected_revision=6)
                run_skill_plan.bind(self.run, expected_revision=6)
                council_selection.bind(self.run, expected_revision=6)
                council_request_policy.bind(self.run, expected_revision=6)
                with (
                    patch.object(
                        model_context, "OPENROUTER_API_URL", config.OPENROUTER_API_URL
                    ),
                    patch.object(
                        model_context, "model_limits", AsyncMock(return_value=LIMITS)
                    ),
                ):
                    asyncio.run(
                        council_model_budgets.bind(self.run, expected_revision=6)
                    )
                run_stage1_inputs.bind(self.run, expected_revision=6)

                async def provider(**kwargs):
                    return STOP

                for model in self.models:
                    asyncio.run(
                        run_stage1_execution.execute(
                            self.run,
                            model,
                            provider,
                            provider_key="test",
                            expected_revision=6,
                        )
                    )
                prompt = durable_response_review.advance(self.run, expected_revision=6)[
                    "current_prompt"
                ]
                run_interaction.answer(
                    self.run, prompt["instance_id"], AnswerRequest(responses={})
                )
                durable_response_review.advance(self.run, expected_revision=8)
                response_review_policy.bind(self.run, expected_revision=9)
                self.revision = 9
                saved = self.bind()
                self.assertEqual(saved.ranking_method, "pairwise")
                self.assertEqual(saved.source_revision, 9)
                self.assert_legacy_parity(saved, refine=True, skipped=skipped_prequery)
                text = saved.messages[0].content
                self.assertEqual(
                    "prioritize actionability" in text, not skipped_prequery
                )
                query = (
                    "Original exact ☃" if skipped_refinement else "Refined exact Пример"
                )
                self.assertIn("Question: " + query, text)

    def test_continued_answer_is_exact_and_never_reexecuted(self):
        from test_durable_response_review import DurableResponseReviewTests

        fixture = DurableResponseReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.complete(continued=True)
        fixture.step(3)
        fixture.answer()
        fixture.step(5)
        self.run, self.models, self.revision = fixture.run, fixture.models, 6
        response_review_policy.bind(self.run, expected_revision=6)
        results = run_stage1_results.load_complete_results(self.run)
        saved = self.bind()
        self.assertEqual(len(results[0].chunks), 2)
        self.assertIn("Response A:\n" + results[0].content, saved.messages[0].content)
        self.assertEqual(run_stage1_results.load_complete_results(self.run), results)
        self.assert_legacy_parity(saved)
