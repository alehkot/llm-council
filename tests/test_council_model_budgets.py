"""Offline pinned model metadata, races, historical reads and budget parity."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import test_council_request_policy as fixtures

from backend import (
    config,
    council_request_policy,
    council_selection,
    model_context,
    openrouter,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    storage,
)
from backend import (
    council_model_budgets as budgets,
)

LIMITS = {
    "context_length": 32768,
    "max_completion_tokens": 4096,
    "supported_parameters": ["temperature", "reasoning"],
}
REAL_MODEL_LIMITS = model_context.model_limits


class CouncilModelBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.CouncilRequestPolicyTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.run = self.fixture.run
        self.resolver = AsyncMock(return_value=copy.deepcopy(LIMITS))
        for target, key, value in (
            (model_context, "model_limits", self.resolver),
            (model_context, "OPENROUTER_API_URL", config.OPENROUTER_API_URL),
        ):
            p = patch.object(target, key, value)
            p.start()
            self.addCleanup(p.stop)

    def complete(self):
        self.fixture.complete()
        self.fixture.bind()

    async def bind(self, revision=3):
        return await budgets.bind(self.run, expected_revision=revision)

    async def test_exact_limits_budgets_sources_and_cursor(self):
        self.complete()
        before = run_inspection.inspect(self.run)
        saved = await self.bind()
        self.assertEqual([m.model for m in saved.models], config.COUNCIL_MODELS)
        self.assertEqual(self.resolver.await_count, 2)
        for model in saved.models:
            self.assertEqual(model.context_length, 32768)
            self.assertEqual(model.max_completion_tokens, 4096)
            self.assertEqual(model.supported_parameters, ("temperature", "reasoning"))
            self.assertEqual(model.output_budget, 1234)
            self.assertEqual(model.input_budget, 32768 - 1234 - 1638)
            self.assertTrue(model.reasoning)
        self.assertEqual(saved.source_revision, 3)
        self.assertEqual(
            saved.request_policy_sha256, budgets._identity(self.fixture.bind())
        )
        after = run_inspection.inspect(self.run)
        self.assertEqual(after["council_model_budgets"], saved.model_dump(mode="json"))
        self.assertNotEqual(before["cursor"], after["cursor"])
        for key in (
            "interaction",
            "request",
            "council_selection",
            "council_request_policy",
        ):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after["internal_phase"], "pre_query_complete")

    async def test_repeat_bind_and_historical_read_do_not_resolve(self):
        self.complete()
        saved = await self.bind()
        before = self.fixture.files()
        self.resolver.side_effect = AssertionError("Metadata must remain pinned")
        self.assertEqual(await self.bind(), saved)
        with patch.object(
            council_request_policy, "_current", side_effect=AssertionError
        ):
            self.assertEqual(
                run_inspection.inspect(self.run)["council_model_budgets"],
                saved.model_dump(mode="json"),
            )
        self.assertEqual(self.fixture.files(), before)

    async def test_nested_values_are_frozen_detached_and_secret_free(self):
        self.complete()
        with patch.object(config, "OPENROUTER_API_KEY", "synthetic-secret"):
            saved = await self.bind()
        self.resolver.return_value["supported_parameters"].clear()
        with self.assertRaises(ValueError):
            saved.models[0].input_budget = 1
        with self.assertRaises(ValueError):
            saved.models = ()
        copy_value = saved.model_dump(mode="json")
        copy_value["models"][0]["supported_parameters"].clear()
        self.assertEqual(len(saved.models[0].supported_parameters), 2)
        self.assertNotIn("synthetic-secret", run_admission._path(self.run).read_text())
        self.assertEqual(await self.bind(), saved)

    async def test_invalid_resolver_values_fail_without_writes(self):
        self.complete()
        invalid = [None, [], 4, {}, {**LIMITS, "extra": 1}]
        for key, values in {
            "context_length": [0, -1, True, 8000.0, "8000", 256, 1],
            "max_completion_tokens": [0, -1, True, "100", 100.0],
            "supported_parameters": [
                None,
                "reasoning",
                [1],
                [None],
                [""],
                ["x", "x"],
                [" x"],
                ["x\n"],
            ],
        }.items():
            invalid.extend({**LIMITS, key: value} for value in values)
        for value in invalid:
            with self.subTest(value=value):
                self.resolver.return_value = value
                before = self.fixture.files()
                with self.assertRaises(budgets.ModelBudgetConflict):
                    await self.bind()
                self.assertEqual(before, self.fixture.files())

    async def test_missing_prerequisites_never_call_resolver(self):
        with self.assertRaises(budgets.ModelBudgetConflict):
            await self.bind(revision=0)
        self.fixture.complete(selection=False)
        with self.assertRaises(budgets.ModelBudgetConflict):
            await self.bind()
        council_selection.bind(self.run, expected_revision=3)
        with self.assertRaises(budgets.ModelBudgetConflict):
            await self.bind()
        self.resolver.assert_not_called()

    async def test_stale_invalid_revisions_are_rejected_before_resolution(self):
        self.complete()
        for revision in (0, 2, 4):
            with self.assertRaises(run_interaction.PromptConflict):
                await self.bind(revision)
        for revision in (-1, True, 3.0, "3"):
            with self.assertRaises(ValueError):
                await self.bind(revision)
        self.resolver.assert_not_called()

    async def test_config_drift_rejects_initial_and_repeat_binds(self):
        self.complete()
        for already_bound in (False, True):
            if already_bound:
                await self.bind()
            for key, value in (
                ("COUNCIL_MODELS", ["different/model"]),
                ("OPENROUTER_MAX_TOKENS", 99),
                ("OPENROUTER_API_URL", "https://different.example/chat"),
                ("MODEL_BACKEND", "acp"),
            ):
                before = self.fixture.files()
                calls = self.resolver.await_count
                with patch.object(config, key, value):
                    with self.assertRaises(budgets.ModelBudgetConflict):
                        await self.bind()
                    run_inspection.inspect(self.run)
                self.assertEqual(before, self.fixture.files())
                self.assertEqual(calls, self.resolver.await_count)
        with patch.object(model_context, "OPENROUTER_API_URL", "https://other.example"):
            with self.assertRaises(budgets.ModelBudgetConflict):
                await self.bind()

    async def test_concurrent_resolvers_first_persisted_values_win(self):
        self.complete()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def resolve(model):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
                return {**LIMITS, "context_length": 65536}
            return copy.deepcopy(LIMITS)

        self.resolver.side_effect = resolve
        write = run_admission._write
        with patch.object(run_admission, "_write", wraps=write) as writes:
            first = asyncio.create_task(self.bind())
            await entered.wait()
            second = await self.bind()
            release.set()
            self.assertEqual(await first, second)
            self.assertEqual(writes.call_count, 1)
        self.assertEqual(second.models[0].context_length, 32768)

    async def test_cancellation_during_resolution_prevents_late_write(self):
        self.complete()

        async def resolve(model):
            run_cancellation.request(self.run)
            return copy.deepcopy(LIMITS)

        self.resolver.side_effect = resolve
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.bind()
        snap = run_inspection.inspect(self.run)
        self.assertEqual(snap["internal_phase"], "cancelled")
        self.assertIsNone(snap["council_model_budgets"])

    async def test_deletion_during_resolution_prevents_resurrection(self):
        self.complete()

        async def resolve(model):
            storage.delete_conversation("cli-" + self.run)
            return copy.deepcopy(LIMITS)

        self.resolver.side_effect = resolve
        with self.assertRaises(run_admission.AdmissionDeleted):
            await self.bind()
        self.assertNotIn("council_model_budgets", run_admission.get(self.run))

    async def test_configuration_change_while_awaiting_fails_closed(self):
        self.complete()

        async def resolve(model):
            config.OPENROUTER_MAX_TOKENS = 99
            return copy.deepcopy(LIMITS)

        self.resolver.side_effect = resolve
        before = self.fixture.files()
        with self.assertRaises(budgets.ModelBudgetConflict):
            await self.bind()
        self.assertEqual(before, self.fixture.files())

    async def test_cancelled_resolver_and_failure_leave_no_partial_snapshot(self):
        self.complete()
        for failure in (RuntimeError("catalog unavailable"), asyncio.CancelledError()):
            self.resolver.side_effect = [copy.deepcopy(LIMITS), failure]
            before = self.fixture.files()
            with self.assertRaises(type(failure)):
                await self.bind()
            self.assertEqual(before, self.fixture.files())

    async def test_write_failures_and_lost_acknowledgment(self):
        self.complete()
        before = self.fixture.files()
        with patch.object(run_admission, "_write", side_effect=OSError("before write")):
            with self.assertRaises(OSError):
                await self.bind()
        self.assertEqual(before, self.fixture.files())
        write = run_admission._write

        def lost_ack(record):
            write(record)
            raise OSError("after write")

        with patch.object(run_admission, "_write", side_effect=lost_ack):
            with self.assertRaises(OSError):
                await self.bind()
        before = self.fixture.files()
        self.resolver.side_effect = AssertionError("Do not refresh")
        self.assertEqual(
            (await self.bind()).model_dump(mode="json"),
            run_inspection.inspect(self.run)["council_model_budgets"],
        )
        self.assertEqual(before, self.fixture.files())

    async def test_saved_corruption_is_rejected_without_repair(self):
        self.complete()
        await self.bind()
        path = run_admission._path(self.run)
        original = path.read_bytes()
        mutations = [
            lambda r: r.update(council_model_budgets=None),
            lambda r: r["council_model_budgets"].pop("version"),
            lambda r: r["council_model_budgets"].update(version=2),
            lambda r: r["council_model_budgets"].update(source_revision=2),
            lambda r: r["council_model_budgets"].update(request_policy_sha256="0" * 64),
            lambda r: r["council_model_budgets"].update(models=[]),
            lambda r: r["council_model_budgets"]["models"].reverse(),
            lambda r: r["council_model_budgets"]["models"][0].update(input_budget=9),
            lambda r: r["council_model_budgets"]["models"][0].update(output_budget=9),
            lambda r: r["council_model_budgets"]["models"][0].update(reasoning=False),
            lambda r: r["council_model_budgets"]["models"][0].pop("context_length"),
            lambda r: r["council_model_budgets"]["models"][0].update(extra=1),
            lambda r: r["council_model_budgets"]["models"][0].update(
                supported_parameters=None
            ),
            lambda r: r.pop("council_request_policy"),
            lambda r: r["council_request_policy"].update(max_tokens=100),
        ]
        for mutate in mutations:
            path.write_bytes(original)
            self.fixture.rewrite(mutate)
            before = self.fixture.files()
            with self.assertRaises(run_admission.AdmissionUnavailable):
                run_inspection.inspect(self.run)
            self.assertEqual(before, self.fixture.files())

    async def test_saved_cancelled_history_is_readable_but_cannot_bind(self):
        self.complete()
        saved = await self.bind()
        run_cancellation.request(self.run)
        before = self.fixture.files()
        self.assertEqual(
            run_inspection.inspect(self.run)["council_model_budgets"],
            saved.model_dump(mode="json"),
        )
        with self.assertRaises(run_admission.AdmissionCancelled):
            await self.bind()
        self.assertEqual(before, self.fixture.files())

    async def test_fresh_process_reads_without_config_or_metadata_resolution(self):
        self.complete()
        saved = await self.bind()
        before = self.fixture.files()
        script = """
import json, sys
from unittest.mock import patch
from backend import storage, run_inspection, model_context, council_request_policy
storage.DATA_DIR = sys.argv[1]
with patch.object(model_context, 'model_limits', side_effect=AssertionError), patch.object(council_request_policy, '_current', side_effect=AssertionError):
    print(json.dumps(run_inspection.inspect(sys.argv[2])['council_model_budgets']))
"""
        result = subprocess.run(
            [sys.executable, "-c", script, self.fixture.data, self.run],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertEqual(json.loads(result.stdout), saved.model_dump(mode="json"))
        self.assertEqual(before, self.fixture.files())

    async def test_budget_formula_matches_legacy_request_parameters(self):
        for context, completion, maximum, parameters in (
            (8192, 4096, 16000, []),
            (100000, 500, 16000, ["reasoning"]),
            (32768, 4096, 1234, ["temperature"]),
            (1025, 1000, 256, ["reasoning"]),
        ):
            limits = dict(
                context_length=context,
                max_completion_tokens=completion,
                supported_parameters=parameters,
            )
            expected = budgets._budget("fake", limits, maximum)
            fit = AsyncMock(return_value=([{"role": "user", "content": "question"}], 0))
            request = AsyncMock(
                return_value={"content": "answer", "finish_reason": "stop"}
            )
            with (
                patch.object(
                    openrouter, "model_limits", AsyncMock(return_value=limits)
                ),
                patch.object(openrouter, "OPENROUTER_MAX_TOKENS", maximum),
                patch.object(openrouter, "fit_messages", fit),
                patch.object(openrouter, "_query_model_once", request),
            ):
                await openrouter.query_model(
                    "fake", [{"role": "user", "content": "question"}]
                )
            self.assertEqual(fit.await_args.args[1], expected.input_budget)
            self.assertEqual(
                request.await_args.kwargs["max_tokens"], expected.output_budget
            )
            self.assertEqual(request.await_args.kwargs["reasoning"], expected.reasoning)

    async def test_real_resolver_catalog_and_unknown_fallback_are_pinned(self):
        self.complete()
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "provider/first",
                            "context_length": 65536,
                            "top_provider": {
                                "context_length": 16384,
                                "max_completion_tokens": 512,
                            },
                            "supported_parameters": ["reasoning"],
                        }
                    ]
                },
            )

        real_client = httpx.AsyncClient

        def client(**kwargs):
            return real_client(**kwargs, transport=httpx.MockTransport(respond))

        with (
            patch.object(model_context, "model_limits", REAL_MODEL_LIMITS),
            patch.object(model_context, "_refresh_after", 0),
            patch.object(model_context, "_catalog", {}),
            patch.object(model_context, "_snapshot", {}),
            patch.object(model_context.httpx, "AsyncClient", client),
        ):
            saved = await self.bind()
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].method, "GET")
            self.assertEqual(str(calls[0].url), config.OPENROUTER_API_URL + "/models")
            self.assertNotIn("authorization", calls[0].headers)
            self.assertEqual(saved.models[0].context_length, 16384)
            self.assertEqual(saved.models[0].output_budget, 512)
            self.assertTrue(saved.models[0].reasoning)
            self.assertEqual(saved.models[1].context_length, 8192)
            self.assertFalse(saved.models[1].reasoning)
            self.assertEqual(await self.bind(), saved)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
