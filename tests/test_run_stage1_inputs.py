"""Offline exact initial requests, legacy parity and durable preparation guards."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import test_council_request_policy as request_fixtures
import test_run_skill_plan as skill_fixtures

from backend import (
    config,
    council,
    council_model_budgets,
    council_request_policy,
    council_selection,
    durable_prequery,
    langgraph_flow,
    model_context,
    openrouter,
    prequery_policy,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    run_skill_plan,
    run_stage1_inputs,
    skills,
    storage,
)
from backend.consultation_contract import AnswerRequest
from backend.interactive import InteractiveConfig, PromptResponse

LIMITS = {
    "context_length": 32768,
    "max_completion_tokens": 4096,
    "supported_parameters": ["reasoning"],
}


class Stage1InputTests(unittest.TestCase):
    def setUp(self):
        self.fixture = skill_fixtures.DurableSkillPlanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.run, self.root = self.fixture.run, self.fixture.root
        for key, value in {
            "MODEL_BACKEND": "openrouter",
            "COUNCIL_MODELS": ["provider/first", "provider/second"],
            "NEGATIVE_MODE_ENABLED": False,
            "DELIBERATION_ENABLED": True,
            "PAIRWISE_RANKING_ENABLED": False,
            **{key: v for key, (_, v) in request_fixtures.SETTINGS.items()},
        }.items():
            p = patch.object(config, key, value)
            p.start()
            self.addCleanup(p.stop)

    def complete(self, skipped=False):
        prompt = durable_prequery.advance(self.run, expected_revision=0)[
            "current_prompt"
        ]
        run_interaction.answer(
            self.run,
            prompt["instance_id"],
            AnswerRequest(skipped=skipped, responses={}),
        )
        durable_prequery.advance(self.run, expected_revision=2)
        self.response = prequery_policy.bind(self.run, expected_revision=3)
        self.skills = run_skill_plan.bind(self.run, expected_revision=3)
        council_selection.bind(self.run, expected_revision=3)
        self.request = council_request_policy.bind(self.run, expected_revision=3)
        with (
            patch.object(
                model_context, "OPENROUTER_API_URL", config.OPENROUTER_API_URL
            ),
            patch.object(
                model_context,
                "model_limits",
                AsyncMock(side_effect=[LIMITS, {**LIMITS, "supported_parameters": []}]),
            ),
        ):
            self.budgets = asyncio.run(
                council_model_budgets.bind(self.run, expected_revision=3)
            )

    def bind(self, revision=3):
        return run_stage1_inputs.bind(self.run, expected_revision=revision)

    def files(self):
        return self.fixture.files()

    def test_exact_messages_payloads_sources_and_compact_cursor(self):
        self.complete()
        before = run_inspection.inspect(self.run)
        plan = self.bind()
        self.assertEqual(plan.endpoint, self.request.endpoint)
        self.assertEqual(plan.timeout_seconds, 120.0)
        self.assertEqual(plan.source_revision, 3)
        for name, source in (
            ("response_policy", self.response),
            ("skill_plan", self.skills),
            ("model_budgets", self.budgets),
            ("request_policy", self.request),
        ):
            self.assertEqual(
                getattr(plan, name + "_sha256"), run_stage1_inputs._digest(source)
            )
        messages = [m.model_dump(mode="json") for m in plan.messages]
        self.assertEqual(
            messages,
            [
                {
                    "role": "system",
                    "content": self.skills.stage1.text.strip()
                    + "\n\n"
                    + self.response.system_prompt_modifier.strip(),
                },
                {"role": "user", "content": self.response.user_query},
            ],
        )
        self.assertEqual(plan.input_token_bound, model_context.token_bound(messages))
        for index, request in enumerate(plan.requests):
            expected = {
                "model": config.COUNCIL_MODELS[index],
                "messages": messages,
                "max_tokens": 1234,
            }
            if index == 0:
                expected["reasoning"] = {"effort": "high"}
            self.assertEqual(request.payload(), expected)
            self.assertEqual(
                request.input_budget, self.budgets.models[index].input_budget
            )
            self.assertEqual(
                request.output_budget, self.budgets.models[index].output_budget
            )
        after = run_inspection.inspect(self.run)
        self.assertEqual(after["stage1_inputs"], run_stage1_inputs.summary(plan))
        self.assertNotEqual(before["cursor"], after["cursor"])
        self.assertNotIn("Original instructions", json.dumps(after))
        for key in (
            "interaction",
            "internal_phase",
            "request",
            "skill_plan",
            "council_model_budgets",
        ):
            self.assertEqual(before[key], after[key])

    def test_actual_legacy_stage1_and_openrouter_payloads_match_without_network(self):
        self.complete()
        plan = self.bind()
        context = SimpleNamespace(
            skills_runtime=SimpleNamespace(stage1_block=self.skills.stage1.text),
            collected_responses={
                "pre_query_config": PromptResponse(
                    prompt_id="pre_query_config", responses={}
                )
            },
            interactive_config=InteractiveConfig.from_responses(
                {
                    "pre_query_config": PromptResponse(
                        prompt_id="pre_query_config", responses={}
                    )
                }
            ),
        )
        system = langgraph_flow._stage1_system_prompt(context)
        calls = []

        async def post(url, *, headers, json):
            calls.append((url, copy.deepcopy(json)))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "synthetic answer"},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.side_effect = post
        with (
            patch.object(council, "COUNCIL_MODELS", list(config.COUNCIL_MODELS)),
            patch.object(council, "query_model", openrouter.query_model),
            patch.object(openrouter, "OPENROUTER_MAX_TOKENS", 1234),
            patch.object(openrouter, "OPENROUTER_API_URL", plan.endpoint),
            patch.object(
                openrouter,
                "model_limits",
                AsyncMock(side_effect=[LIMITS, {**LIMITS, "supported_parameters": []}]),
            ),
            patch.object(
                openrouter.httpx, "AsyncClient", return_value=client
            ) as clients,
        ):
            answers = asyncio.run(
                council.stage1_collect_responses(
                    self.response.user_query, system_prompt=system
                )
            )
        self.assertEqual([r["response"] for r in answers], ["synthetic answer"] * 2)
        self.assertEqual(calls, [(plan.endpoint, r.payload()) for r in plan.requests])
        self.assertEqual(clients.call_count, 2)
        for call in clients.call_args_list:
            self.assertEqual(call.kwargs, {"timeout": plan.timeout_seconds})

    def test_initial_and_repeat_binds_do_not_resolve_call_providers_or_read_library(
        self,
    ):
        self.complete()
        skills.delete_skill("one")
        with (
            patch.object(
                model_context, "model_limits", side_effect=AssertionError("catalog")
            ),
            patch.object(
                openrouter, "query_model", side_effect=AssertionError("provider")
            ),
            patch.object(
                model_context, "fit_messages", side_effect=AssertionError("reduction")
            ),
            patch.object(skills, "get_skill", side_effect=AssertionError("library")),
        ):
            saved = self.bind()
            before = self.files()
            self.assertEqual(self.bind(), saved)
            self.assertEqual(run_stage1_inputs.load(self.run), saved)
            self.assertEqual(before, self.files())

    def test_missing_prerequisites_are_not_implicitly_created(self):
        before = self.files()
        with self.assertRaises(run_stage1_inputs.Stage1InputConflict):
            self.bind(0)
        self.assertEqual(before, self.files())
        self.complete()
        path = run_admission._path(self.run)
        original = path.read_bytes()
        for key in (
            "pre_query_policy",
            "skill_plan",
            "council_request_policy",
            "council_model_budgets",
            "council_selection",
            "explicit_skill_snapshot",
        ):
            raw = json.loads(original)
            raw.pop(key)
            path.write_text(json.dumps(raw))
            before = self.files()
            with self.assertRaises(
                (
                    run_stage1_inputs.Stage1InputConflict,
                    run_admission.AdmissionUnavailable,
                )
            ):
                self.bind()
            self.assertEqual(before, self.files())
        path.write_bytes(original)

    def test_invalid_and_stale_revisions_write_nothing(self):
        self.complete()
        for revision in (0, 2, 4, -1, True, "3", 3.0, None):
            before = self.files()
            with self.assertRaises(ValueError):
                self.bind(revision)
            self.assertEqual(before, self.files())

    def test_current_configuration_drift_blocks_initial_and_repeat_binding(self):
        self.complete()
        for saved in (False, True):
            if saved:
                history = self.bind()
            for key, value in (
                ("COUNCIL_MODELS", ["provider/new"]),
                ("MODEL_BACKEND", "acp"),
                ("NEGATIVE_MODE_ENABLED", True),
                ("OPENROUTER_API_URL", "https://changed.example/chat"),
                ("OPENROUTER_MAX_TOKENS", 88),
                ("OPENROUTER_MAX_RETRIES", 1),
                ("SKILLS_ENABLED", False),
                ("SKILLS_REFERENCE_SELECTION_ENABLED", True),
                ("SKILLS_MAX_REFERENCES", 1),
                ("SKILLS_CONTEXT_MAX_CHARS", 3500),
            ):
                before = self.files()
                with patch.object(config, key, value):
                    with self.assertRaises(run_stage1_inputs.Stage1InputConflict):
                        self.bind()
                    if saved:
                        self.assertEqual(run_stage1_inputs.load(self.run), history)
                self.assertEqual(before, self.files())

    def test_legacy_imported_globals_credentials_and_catalog_changes_do_not_replan(
        self,
    ):
        self.complete()
        plan = self.bind()
        with (
            patch.object(openrouter, "OPENROUTER_MAX_TOKENS", 9999),
            patch.object(openrouter, "OPENROUTER_API_URL", "https://other.example"),
            patch.object(model_context, "OPENROUTER_API_URL", "https://other.example"),
            patch.object(config, "OPENROUTER_API_KEY", "SYNTHETIC-SECRET"),
            patch.object(
                model_context, "model_limits", side_effect=AssertionError("refresh")
            ),
        ):
            self.assertEqual(self.bind(), plan)
        self.assertNotIn("SYNTHETIC-SECRET", run_admission._path(self.run).read_text())

    def test_empty_skill_block_and_skipped_prequery_omit_system_message(self):
        with patch.object(config, "SKILLS_CONTEXT_MAX_CHARS", 0):
            self.complete(skipped=True)
            plan = self.bind()
        self.assertEqual(
            [m.model_dump(mode="json") for m in plan.messages],
            [{"role": "user", "content": self.response.user_query}],
        )
        self.assertEqual(run_stage1_inputs.load(self.run), plan)

    def test_exact_budget_boundary_succeeds_and_one_smaller_member_prevents_partial_write(
        self,
    ):
        self.complete()
        path = run_admission._path(self.run)
        original = path.read_bytes()
        raw = json.loads(original)
        derived = run_stage1_inputs._derive(raw, run_interaction._validate_record(raw))
        bound = derived.input_token_bound

        def budget_for(target):
            for context in range(target, target * 3):
                try:
                    candidate = council_model_budgets._budget(
                        "provider/second",
                        {
                            **LIMITS,
                            "context_length": context,
                            "supported_parameters": [],
                        },
                        1234,
                    )
                except ValueError:
                    continue
                if candidate.input_budget == target:
                    return candidate.model_dump(mode="json")
            self.fail("Cannot find boundary budget")

        raw["council_model_budgets"]["models"][1] = budget_for(bound - 1)
        path.write_text(json.dumps(raw))
        before = self.files()
        with self.assertRaisesRegex(
            run_stage1_inputs.Stage1InputConflict, "context reduction"
        ):
            self.bind()
        self.assertEqual(before, self.files())
        self.assertNotIn("stage1_inputs", run_admission.get(self.run))
        raw["council_model_budgets"]["models"][1] = budget_for(bound)
        path.write_text(json.dumps(raw))
        plan = self.bind()
        self.assertEqual(plan.requests[1].input_budget, plan.input_token_bound)

    def test_nested_values_are_immutable_detached_and_repr_is_private(self):
        self.complete()
        plan = self.bind()
        with self.assertRaises(ValueError):
            plan.messages[0].content = "changed"
        with self.assertRaises(ValueError):
            plan.requests[0].payload_json = "{}"
        copy_payload = plan.requests[0].payload()
        copy_payload["messages"][0]["content"] = "changed"
        self.assertNotIn("Original instructions", repr(plan))
        self.assertNotIn("Original instructions", repr(plan.messages[0]))
        self.assertNotIn("Original instructions", repr(plan.requests[0]))
        self.assertEqual(run_stage1_inputs.load(self.run), plan)
        self.assertNotEqual(copy_payload, plan.requests[0].payload())

    def test_concurrent_duplicates_write_once(self):
        self.complete()
        with (
            patch.object(run_admission, "_write", wraps=run_admission._write) as writes,
            ThreadPoolExecutor(max_workers=8) as pool,
        ):
            plans = list(pool.map(lambda _: self.bind(), range(24)))
        self.assertEqual(writes.call_count, 1)
        self.assertTrue(all(p == plans[0] for p in plans))

    def test_write_failure_and_lost_acknowledgment(self):
        self.complete()
        before = self.files()
        with patch.object(run_admission, "_write", side_effect=OSError("before write")):
            with self.assertRaises(OSError):
                self.bind()
        self.assertEqual(before, self.files())
        original = run_admission._write

        def lost(record):
            original(record)
            raise OSError("lost acknowledgement")

        with patch.object(run_admission, "_write", side_effect=lost):
            with self.assertRaises(OSError):
                self.bind()
        plan = run_stage1_inputs.load(self.run)
        before = self.files()
        self.assertEqual(self.bind(), plan)
        self.assertEqual(before, self.files())

    def test_cancellation_before_binding_writes_nothing(self):
        self.complete()
        run_cancellation.request(self.run)
        before = self.files()
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()
        self.assertEqual(before, self.files())
        self.assertNotIn("stage1_inputs", run_admission.get(self.run))

    def test_cancellation_preserves_historical_requests_but_rejects_rebind(self):
        self.complete()
        plan = self.bind()
        run_cancellation.request(self.run)
        before = self.files()
        self.assertEqual(run_stage1_inputs.load(self.run), plan)
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()
        self.assertEqual(before, self.files())

    def test_deletion_erases_requests_without_resurrection(self):
        self.complete()
        self.bind()
        storage.delete_conversation("cli-" + self.run)
        before = self.files()
        for call in (self.bind, lambda: run_stage1_inputs.load(self.run)):
            with self.assertRaises(run_admission.AdmissionDeleted):
                call()
        self.assertEqual(before, self.files())
        text = run_admission._path(self.run).read_text()
        self.assertNotIn("stage1_inputs", text)
        self.assertNotIn("Original instructions", text)

    def test_cancel_and_delete_serialize_with_derivation(self):
        for action in ("cancel", "delete"):
            test = Stage1InputTests()
            test.setUp()
            try:
                test.complete()
                entered, release, mutating = (threading.Event() for _ in range(3))
                original = run_stage1_inputs._derive

                def blocked(*args):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("timeout")
                    return original(*args)

                def mutate():
                    mutating.set()
                    return (
                        run_cancellation.request(test.run)
                        if action == "cancel"
                        else storage.delete_conversation("cli-" + test.run)
                    )

                with (
                    patch.object(run_stage1_inputs, "_derive", side_effect=blocked),
                    ThreadPoolExecutor(max_workers=2) as pool,
                ):
                    preparation = pool.submit(test.bind)
                    self.assertTrue(entered.wait(5))
                    mutation = pool.submit(mutate)
                    self.assertTrue(mutating.wait(5))
                    self.assertFalse(mutation.done())
                    release.set()
                    plan = preparation.result(timeout=5)
                    mutation.result(timeout=5)
                if action == "cancel":
                    self.assertEqual(run_stage1_inputs.load(test.run), plan)
                else:
                    self.assertNotIn("stage1_inputs", run_admission.get(test.run))
            finally:
                test.doCleanups()

    def test_nested_corruption_and_broken_sources_fail_closed(self):
        self.complete()
        self.bind()
        path = run_admission._path(self.run)
        original = path.read_bytes()
        mutations = [
            lambda r: r.update(phase="initializing"),
            lambda r: r.pop("skill_plan"),
            lambda r: r.pop("council_model_budgets"),
            lambda r: r["stage1_inputs"].update(version=True),
            lambda r: r["stage1_inputs"].update(version=2),
            lambda r: r["stage1_inputs"].update(source_revision=4),
            lambda r: r["stage1_inputs"].update(endpoint="https://changed.example"),
            lambda r: r["stage1_inputs"].update(timeout_seconds=1),
            lambda r: r["stage1_inputs"].update(input_token_bound=1),
            lambda r: r["stage1_inputs"].update(messages=[]),
            lambda r: r["stage1_inputs"]["messages"][0].update(content="changed"),
            lambda r: r["stage1_inputs"]["messages"][0].update(role="developer"),
            lambda r: r["stage1_inputs"].update(requests=[]),
            lambda r: r["stage1_inputs"]["requests"].reverse(),
            lambda r: r["stage1_inputs"]["requests"][0].update(payload_json="{}"),
            lambda r: r["stage1_inputs"]["requests"][0].update(model="provider/other"),
            lambda r: r["stage1_inputs"]["requests"][0].update(input_budget=1),
            lambda r: r["stage1_inputs"]["requests"][0].update(output_budget=True),
            lambda r: r["stage1_inputs"].update(extra="unexpected"),
        ]
        for name in (
            "response_policy",
            "skill_plan",
            "model_budgets",
            "request_policy",
        ):
            mutations.append(
                lambda r, key=name: r["stage1_inputs"].update(
                    {key + "_sha256": "0" * 64}
                )
            )
        for mutate in mutations:
            raw = json.loads(original)
            mutate(raw)
            path.write_text(json.dumps(raw))
            before = self.files()
            for call in (
                self.bind,
                lambda: run_stage1_inputs.load(self.run),
                lambda: run_inspection.inspect(self.run),
            ):
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    call()
            self.assertEqual(before, self.files())
        path.write_bytes(original)

    def test_fresh_process_read_uses_saved_settings_and_content_only(self):
        self.complete()
        plan = self.bind()
        skills.delete_skill("one")
        before = self.files()
        code = """
import json, sys
from unittest.mock import patch
from backend import council_selection, council_request_policy, model_context, run_skill_plan, run_stage1_inputs, skills, storage
storage.DATA_DIR = sys.argv[1]
with patch.object(council_selection, '_current', side_effect=AssertionError('config')), patch.object(council_request_policy, '_current', side_effect=AssertionError('request')), patch.object(run_skill_plan, '_current', side_effect=AssertionError('skills config')), patch.object(model_context, 'model_limits', side_effect=AssertionError('catalog')), patch.object(skills, 'get_skill', side_effect=AssertionError('library')):
    result = run_stage1_inputs.load(sys.argv[2])
print(json.dumps(result.model_dump(mode='json')))
"""
        result = subprocess.run(
            [sys.executable, "-c", code, storage.DATA_DIR, self.run],
            text=True,
            capture_output=True,
            check=True,
            env={**os.environ, "PYTHONPATH": os.getcwd()},
        )
        self.assertEqual(json.loads(result.stdout), plan.model_dump(mode="json"))
        self.assertEqual(before, self.files())

    def test_unknown_runs_do_not_create_state(self):
        unknown = str(uuid4())
        before = self.files()
        self.assertIsNone(run_inspection.inspect(unknown))
        for call in (
            lambda: run_stage1_inputs.bind(unknown, expected_revision=0),
            lambda: run_stage1_inputs.load(unknown),
        ):
            with self.assertRaises((ValueError, run_admission.AdmissionUnavailable)):
                call()
        self.assertEqual(before, self.files())


if __name__ == "__main__":
    unittest.main()
