"""Offline whole-roster state, coherent result reads and continuation completion."""

import asyncio
import copy
import itertools
import json
import os
import subprocess
import sys
import unittest
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import test_run_stage1_continuation_inputs as fixtures

from backend import (
    config,
    model_context,
    run_admission,
    run_cancellation,
    run_inspection,
    run_stage1_continuation_execution,
    run_stage1_continuation_inputs,
    run_stage1_execution,
    run_stage1_inputs,
    run_stage1_results,
    skills,
    storage,
)

STOP = {
    "content": "  Private synthetic answer Пример ☃\n",
    "finish_reason": "stop",
    "reasoning_details": [{"nested": [1, {"private": True}]}],
    "usage": {"private_usage": [None, False]},
}


class Stage1RosterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.FirstContinuationInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.initial()
        self.run_id = self.fixture.run_id
        self.models = self.fixture.models
        self.path = self.fixture.path
        self.calls = []

    def snapshot(self) -> dict[str, Any]:
        return run_inspection.inspect(self.run_id)

    def summary(self) -> dict[str, Any]:
        return self.snapshot()["stage1"]

    def results(self) -> Any:
        return run_stage1_results.load_complete_results(self.run_id)

    async def execute(
        self, index: int, response: Any = None, *, turn: int = 0, provider: Any = None
    ) -> Any:
        async def fake(**kwargs: Any) -> Any:
            self.calls.append(kwargs)
            if isinstance(response, BaseException):
                raise response
            return copy.deepcopy(STOP if response is None else response)

        module = (
            run_stage1_execution if turn == 0 else run_stage1_continuation_execution
        )
        kwargs = {} if turn == 0 else {"turn": turn}
        return await module.execute(
            self.run_id,
            self.models[index],
            provider or fake,
            provider_key="synthetic-initial" if turn == 0 else "synthetic-continuation",
            expected_revision=3,
            **kwargs,
        )

    def initial(self, index: int, response: Any = None) -> Any:
        return asyncio.run(self.execute(index, response))

    def continuation(self, index: int, response: Any = None) -> Any:
        run_stage1_continuation_inputs.bind(
            self.run_id, self.models[index], expected_revision=3
        )
        return asyncio.run(self.execute(index, response, turn=1))

    def test_ready_lists_whole_roster_without_promoting_run(self) -> None:
        before = self.fixture.files()
        state = self.summary()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["source_revision"], 3)
        self.assertEqual(
            state["inputs_sha256"], run_stage1_inputs._digest(self.fixture.inputs)
        )
        self.assertEqual(state["total_members"], len(self.models))
        self.assertEqual(state["counts"]["not_started"], len(self.models))
        self.assertEqual([m["model"] for m in state["members"]], self.models)
        self.assertTrue(
            all(
                m["request_prepared"] and m["turn"] == 0 and m["retry_token"] is None
                for m in state["members"]
            )
        )
        self.assertEqual(self.snapshot()["internal_phase"], "pre_query_complete")
        self.assertIsNone(self.results())
        self.assertEqual(before, self.fixture.files())

    def test_without_saved_inputs_has_no_roster_projection(self) -> None:
        record = self.fixture.saved()
        record.pop("stage1_inputs")
        self.fixture.rewrite(record)
        self.assertIsNone(self.summary())
        with self.assertRaises(run_stage1_execution.ExecutionConflict):
            self.results()

    def test_one_initial_answer_remains_partial_without_quorum(self) -> None:
        self.initial(1)
        state = self.summary()
        self.assertEqual(state["phase"], "partial")
        self.assertEqual(state["counts"]["complete"], 1)
        self.assertEqual(state["counts"]["not_started"], 1)
        self.assertEqual(self.snapshot()["internal_phase"], "stage1_partial")
        self.assertIsNone(self.results())

    def test_all_initial_answers_complete_in_roster_not_finish_order(self) -> None:
        self.initial(1, {**STOP, "content": "second"})
        self.initial(0, {**STOP, "content": "first"})
        before = self.fixture.files()
        self.assertEqual(self.summary()["phase"], "complete")
        self.assertEqual(self.snapshot()["internal_phase"], "stage1_complete")
        self.assertEqual(self.summary()["counts"]["complete"], 2)
        results = self.results()
        self.assertIsInstance(results, tuple)
        self.assertEqual([r.model for r in results], self.models)
        self.assertEqual([r.content for r in results], ["first", "second"])
        self.assertEqual([r.chunks[0].turn for r in results], [0, 0])
        self.assertEqual(before, self.fixture.files())

    def test_completed_continuation_replaces_effective_initial_need(self) -> None:
        self.initial(0, fixtures.PARTIAL)
        self.continuation(0)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["stage1"]["members"][0]["phase"], "complete")
        self.assertEqual(snapshot["stage1"]["members"][0]["turn"], 1)
        self.assertEqual(snapshot["stage1"]["phase"], "partial")
        self.assertEqual(snapshot["internal_phase"], "stage1_partial")
        self.assertEqual(
            snapshot["stage1_execution"]["members"][0]["phase"], "needs_continuation"
        )
        self.assertIsNone(self.results())

    def test_mixed_initial_and_continued_answers_complete_roster(self) -> None:
        self.initial(1, fixtures.PARTIAL)
        self.initial(0)
        self.continuation(1, {**STOP, "content": "  tail\n"})
        self.assertEqual(self.snapshot()["internal_phase"], "stage1_complete")
        self.assertEqual([m["turn"] for m in self.summary()["members"]], [0, 1])
        results = self.results()
        self.assertEqual(results[0].content, STOP["content"])
        self.assertEqual(results[1].content, fixtures.PARTIAL["content"] + "  tail\n")
        self.assertEqual([c.turn for c in results[1].chunks], [0, 1])
        self.assertEqual(results[1].chunks[0].response.model_dump(), fixtures.PARTIAL)
        self.assertEqual(len(self.calls), 3)

    def test_length_continuation_reports_unsupported_second_turn(self) -> None:
        self.initial(0, fixtures.PARTIAL)
        self.continuation(0, fixtures.PARTIAL)
        self.initial(1)
        member = self.summary()["members"][0]
        self.assertEqual(member["phase"], "needs_continuation")
        self.assertEqual(member["turn"], 2)
        self.assertFalse(member["request_prepared"])
        self.assertIsNone(member["retry_token"])
        self.assertEqual(self.snapshot()["internal_phase"], "stage1_needs_continuation")
        self.assertIsNone(self.results())

    def test_continuation_preparation_is_evidence_not_dispatch(self) -> None:
        self.initial(0, fixtures.PARTIAL)
        before = self.summary()
        self.assertFalse(before["members"][0]["request_prepared"])
        first_cursor = self.snapshot()["cursor"]
        self.fixture.bind()
        after = self.summary()
        self.assertEqual(after["members"][0]["turn"], 1)
        self.assertEqual(after["members"][0]["phase"], "needs_continuation")
        self.assertTrue(after["members"][0]["request_prepared"])
        self.assertNotEqual(first_cursor, self.snapshot()["cursor"])
        self.assertEqual(len(self.calls), 1)

    def test_retry_tokens_keep_member_and_turn_identity(self) -> None:
        with self.assertRaises(run_stage1_execution.ProviderFailure):
            self.initial(0, RuntimeError("synthetic"))
        self.initial(1, fixtures.PARTIAL)
        with self.assertRaises(run_stage1_continuation_execution.ProviderFailure):
            self.continuation(1, RuntimeError("synthetic"))
        state = self.summary()
        record = self.fixture.saved()
        self.assertEqual(state["phase"], "needs_retry")
        self.assertEqual(state["counts"]["needs_retry"], 2)
        self.assertEqual([m["turn"] for m in state["members"]], [0, 1])
        self.assertEqual(
            state["members"][0]["retry_token"],
            record["stage1_execution"]["operations"][0]["attempt"],
        )
        self.assertEqual(
            state["members"][1]["retry_token"],
            record["stage1_continuation_execution"]["operations"][0]["attempt"],
        )
        self.assertIsNone(self.snapshot()["retry_token"])
        self.assertIsNone(self.results())

    def test_running_sibling_does_not_hide_retry_counts(self) -> None:
        with self.assertRaises(run_stage1_execution.ProviderFailure):
            self.initial(0, RuntimeError("synthetic"))

        async def scenario() -> None:
            entered, release = asyncio.Event(), asyncio.Event()

            async def provider(**unused: Any) -> Any:
                entered.set()
                await release.wait()
                return copy.deepcopy(STOP)

            task = asyncio.create_task(self.execute(1, provider=provider))
            try:
                await entered.wait()
                state = self.summary()
                self.assertEqual(state["phase"], "running_initial")
                self.assertEqual(state["counts"]["running"], 1)
                self.assertEqual(state["counts"]["needs_retry"], 1)
                self.assertTrue(self.snapshot()["operation_owned"])
                self.assertIsNone(self.results())
            finally:
                release.set()
                await task

        asyncio.run(scenario())
        self.assertEqual(self.summary()["phase"], "needs_retry")

    def test_mixed_active_turns_and_final_completion(self) -> None:
        self.initial(0, fixtures.PARTIAL)
        self.fixture.bind()

        async def scenario() -> None:
            entered = [asyncio.Event(), asyncio.Event()]
            release = asyncio.Event()

            async def provider(**request: Any) -> Any:
                entered[self.models.index(request["payload"]["model"])].set()
                await release.wait()
                return copy.deepcopy(STOP)

            tasks = [
                asyncio.create_task(self.execute(0, turn=1, provider=provider)),
                asyncio.create_task(self.execute(1, provider=provider)),
            ]
            try:
                await asyncio.gather(*(event.wait() for event in entered))
                self.assertEqual(self.summary()["phase"], "running_continuation")
                self.assertEqual(self.summary()["counts"]["running"], 2)
                self.assertEqual([m["turn"] for m in self.summary()["members"]], [1, 0])
                self.assertEqual(
                    self.snapshot()["internal_phase"], "running_stage1_continuation"
                )
            finally:
                release.set()
                await asyncio.gather(*tasks)

        asyncio.run(scenario())
        self.assertEqual(self.snapshot()["internal_phase"], "stage1_complete")
        self.assertFalse(self.snapshot()["operation_owned"])
        self.assertIsNotNone(self.results())

    def test_cancellation_keeps_historical_completion_not_run_completion(self) -> None:
        self.initial(0)
        self.initial(1, fixtures.PARTIAL)
        self.continuation(1)
        results = self.results()
        run_cancellation.request(self.run_id)
        before = self.fixture.files()
        self.assertEqual(self.summary()["phase"], "complete")
        self.assertEqual(self.snapshot()["internal_phase"], "cancelled")
        self.assertEqual(self.results(), results)
        self.assertEqual(before, self.fixture.files())

    def test_cancellation_during_continuation_wins_over_stage_projection(self) -> None:
        self.initial(0, fixtures.PARTIAL)
        self.fixture.bind()

        async def scenario() -> None:
            entered = asyncio.Event()

            async def provider(**unused: Any) -> Any:
                entered.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(self.execute(0, turn=1, provider=provider))
            await entered.wait()
            run_cancellation.request(self.run_id)
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
        self.assertEqual(self.snapshot()["internal_phase"], "cancelled")
        self.assertEqual(self.summary()["members"][0]["phase"], "needs_retry")
        self.assertEqual(self.summary()["members"][0]["turn"], 1)
        self.assertIsNone(self.results())

    def test_read_projection_is_private_detached_and_byte_pure(self) -> None:
        self.initial(0)
        self.initial(1, fixtures.PARTIAL)
        self.continuation(1)
        expected = self.results()
        state = self.summary()
        before = self.fixture.files()
        with (
            patch.object(config, "COUNCIL_MODELS", ["changed"]),
            patch.object(
                run_stage1_inputs, "_check_current", side_effect=AssertionError
            ),
            patch.object(model_context, "model_limits", side_effect=AssertionError),
            patch.object(skills, "list_skills", side_effect=AssertionError),
            patch.object(run_admission, "_write", side_effect=AssertionError),
        ):
            self.assertEqual(self.summary(), state)
            self.assertEqual(self.results(), expected)
        serialized = json.dumps(state)
        for secret in (
            "Private",
            "private_usage",
            "nested",
            "content",
            "payload_json",
            "reasoning_details",
        ):
            self.assertNotIn(secret, serialized)
        state["members"][0]["phase"] = "changed"
        state["counts"]["complete"] = 99
        expected[0].chunks[0].response.usage["private_usage"].append("changed")
        self.assertEqual(self.summary()["counts"]["complete"], 2)
        self.assertNotIn(
            "changed", self.results()[0].chunks[0].response.usage["private_usage"]
        )
        self.assertEqual(before, self.fixture.files())

    def test_ephemeral_ownership_changes_cursor_not_saved_bytes(self) -> None:
        with self.assertRaises(run_stage1_execution.ProviderFailure):
            self.initial(0, RuntimeError("synthetic"))
        before = self.fixture.files()
        idle = self.snapshot()
        key = (self.run_id, idle["stage1"]["members"][0]["retry_token"])
        run_stage1_execution._active.add(key)
        try:
            active = self.snapshot()
            self.assertEqual(active["stage1"]["phase"], "running_initial")
            self.assertIsNone(active["stage1"]["members"][0]["retry_token"])
            self.assertNotEqual(idle["cursor"], active["cursor"])
        finally:
            run_stage1_execution._active.discard(key)
        self.assertEqual(idle, self.snapshot())
        self.assertEqual(before, self.fixture.files())

    def test_corruption_in_sibling_is_not_hidden_by_incomplete_first_member(
        self,
    ) -> None:
        self.initial(1, fixtures.PARTIAL)
        self.continuation(1)
        original = self.path.read_bytes()
        for field in ("inputs_sha256", "attempt", "phase"):
            self.path.write_bytes(original)
            record = self.fixture.saved()
            record["stage1_continuation_execution"]["operations"][0][field] = "broken"
            self.fixture.rewrite(record)
            before = self.fixture.files()
            for read in (self.summary, self.results):
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    read()
            self.assertEqual(before, self.fixture.files())

    def test_unknown_and_deleted_runs_do_not_expose_results(self) -> None:
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_stage1_results.load_complete_results(str(uuid4()))
        storage.delete_conversation("cli-" + self.run_id)
        for read in (self.summary, self.results):
            with self.assertRaises(run_admission.AdmissionDeleted):
                read()

    def test_fresh_process_restores_combined_projection_without_current_sources(
        self,
    ) -> None:
        self.initial(0, fixtures.PARTIAL)
        self.continuation(0)
        self.initial(1)
        expected = {
            "state": self.summary(),
            "results": [r.model_dump(mode="json") for r in self.results()],
        }
        before = self.fixture.files()
        script = """
import json,sys
from unittest.mock import patch
from backend import storage, run_inspection, run_stage1_results, run_stage1_inputs
storage.DATA_DIR=sys.argv[1]
with patch.object(run_stage1_inputs, '_check_current', side_effect=AssertionError):
    print(json.dumps({'state':run_inspection.inspect(sys.argv[2])['stage1'], 'results':[r.model_dump(mode='json') for r in run_stage1_results.load_complete_results(sys.argv[2])]}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script, storage.DATA_DIR, self.run_id],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual(before, self.fixture.files())

    def test_all_49_mixed_member_states_match_complete_result_projection(self) -> None:
        original = self.path.read_bytes()
        states = (
            "not_started",
            "initial_stop",
            "initial_length",
            "continued_stop",
            "continued_length",
            "initial_uncertain",
            "continued_uncertain",
        )
        for pair in itertools.product(states, repeat=2):
            self.path.write_bytes(original)
            for index, state in enumerate(pair):
                if state == "not_started":
                    continue
                if state == "initial_uncertain":
                    with self.assertRaises(run_stage1_execution.ProviderFailure):
                        self.initial(index, RuntimeError("synthetic"))
                elif state == "initial_stop":
                    self.initial(index)
                else:
                    self.initial(index, fixtures.PARTIAL)
                    if state == "continued_stop":
                        self.continuation(index)
                    elif state == "continued_length":
                        self.continuation(index, fixtures.PARTIAL)
                    elif state == "continued_uncertain":
                        with self.assertRaises(
                            run_stage1_continuation_execution.ProviderFailure
                        ):
                            self.continuation(index, RuntimeError("synthetic"))
            before = self.fixture.files()
            summary = self.summary()
            individually_complete = [
                run_stage1_results.load_complete_result(self.run_id, m) is not None
                for m in self.models
            ]
            with self.subTest(pair=pair):
                self.assertEqual(
                    [m["phase"] == "complete" for m in summary["members"]],
                    individually_complete,
                )
                self.assertEqual(
                    summary["counts"]["complete"], sum(individually_complete)
                )
                self.assertEqual(sum(summary["counts"].values()), 2)
                self.assertEqual(self.results() is not None, all(individually_complete))
                self.assertEqual(
                    summary["phase"] == "complete", all(individually_complete)
                )
                self.assertEqual(before, self.fixture.files())


if __name__ == "__main__":
    unittest.main()
