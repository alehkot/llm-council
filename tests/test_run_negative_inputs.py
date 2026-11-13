"""Offline exact negative-stage preparation from real reviewed Stage1 receipts."""

import asyncio
import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_run_stage2_inputs as fixtures

from backend import (
    config,
    council,
    council_model_budgets,
    council_request_policy,
    council_selection,
    model_context,
    prequery_policy,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    run_stage1_inputs,
    run_stage1_results,
    storage,
)
from backend import run_negative_inputs as negative


class NegativeInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.Stage2InputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def complete(self, responses=None, **kwargs):
        self.fixture.complete(
            responses,
            **{"negative": True, "requested": "  Exact concern ☃\n ", **kwargs},
        )
        self.run = self.fixture.run
        self.models = self.fixture.models
        self.path = run_admission._path(self.run)

    def bind(self, revision=6):
        return negative.bind(self.run, expected_revision=revision)

    def test_exact_self_peer_requests_and_saved_provenance(self):
        self.complete()
        before = run_inspection.inspect(self.run)
        saved = self.bind()
        self.assertEqual(saved.active_models, tuple(self.models))
        self.assertEqual([r.model for r in saved.requests], self.models)
        self.assertEqual(saved.negative_prompt_source, "request")
        record, interaction = run_interaction._load(self.run)
        for name, value in {
            "response_policy": prequery_policy.validate_saved(record, interaction),
            "review_policy": self.fixture.policy,
            "selection": council_selection.validate_saved(record, interaction),
            "model_budgets": council_model_budgets.validate_saved(record, interaction),
            "request_policy": council_request_policy.validate_saved(
                record, interaction
            ),
        }.items():
            self.assertEqual(
                getattr(saved, name + "_sha256"), run_stage1_inputs._digest(value)
            )
        self.assertEqual(
            saved.stage1_source_sha256, self.fixture.policy.stage1_source_sha256
        )
        self.assertEqual(saved.source_revision, 6)
        self.assertEqual(saved.timeout_seconds, 120.0)
        for index, request in enumerate(saved.requests):
            payload = request.payload()
            self.assertEqual(request.self_label, f"Response {'AB'[index]}")
            self.assertEqual(request.peer_labels, (f"Response {'BA'[index]}",))
            self.assertEqual(
                request.input_token_bound,
                model_context.token_bound(payload["messages"]),
            )
            self.assertEqual(payload["model"], self.models[index])
            self.assertEqual(payload["max_tokens"], request.output_budget)
            self.assertEqual(
                payload.get("reasoning"), {"effort": "high"} if index == 0 else None
            )
            self.assertIn("  Exact concern ☃\n ", payload["messages"][0]["content"])
        after = run_inspection.inspect(self.run)
        self.assertEqual(after["negative_inputs"], negative.summary(saved))
        self.assertNotIn("Exact concern", json.dumps(after["negative_inputs"]))
        self.assertNotIn("Exact concern", repr(saved))
        self.assertNotEqual(before["cursor"], after["cursor"])
        for key in ("interaction", "internal_phase", "stage1", "response_review"):
            self.assertEqual(before[key], after[key])

    def test_excluded_owner_is_not_asked_and_survivor_relabels(self):
        self.complete({"exclude_responses": ["provider/first"]})
        saved = self.bind()
        self.assertEqual(saved.active_models, (self.models[1],))
        self.assertEqual(saved.label_to_model, {"Response A": self.models[1]})
        self.assertEqual(len(saved.requests), 1)
        self.assertEqual(saved.requests[0].self_label, "Response A")
        self.assertEqual(saved.requests[0].peer_labels, ())
        self.assertNotIn('"reasoning"', saved.requests[0].payload_json)
        self.assertNotIn(self.models[0], saved.requests[0].payload_json)

    def test_prompt_precedence_exact_whitespace_and_review_source(self):
        for request, concern, expected, source in (
            ("  ", "review", "  ", "request"),
            (None, "  concern Пример\n", "  concern Пример\n", "review"),
            ("request", "review", "request", "request"),
        ):
            with self.subTest(source=source, request=request):
                self.complete({"gap_concerns": concern}, requested=request)
                saved = self.bind()
                self.assertEqual(saved.negative_prompt_source, source)
                self.assertIn(
                    "NEGATIVE PROMPT (use this to judge the responses):\n"
                    + expected
                    + "\n\nYOUR RESPONSE",
                    saved.requests[0].payload()["messages"][0]["content"],
                )

    def test_disabled_generation_required_and_all_excluded_do_not_write(self):
        for responses, kwargs in (
            ({}, {"negative": False}),
            ({}, {"requested": None}),
            ({}, {"requested": ""}),
            ({"exclude_responses": ["provider/first", "provider/second"]}, {}),
        ):
            self.complete(responses, **kwargs)
            before = self.path.read_bytes()
            with self.assertRaises(negative.NegativeInputConflict):
                self.bind()
            self.assertEqual(before, self.path.read_bytes())

    def test_legacy_messages_and_trace_context_match(self):
        for excluded in ([], ["provider/first"]):
            self.complete({"exclude_responses": excluded})
            saved = self.bind()
            record, interaction = run_interaction._load(self.run)
            response = prequery_policy.validate_saved(record, interaction)
            active = [
                {"model": r.model, "response": r.content}
                for r in run_stage1_results.load_complete_results(self.run)
                if r.model in saved.active_models
            ]
            labels, _ = council.build_label_mapping(active)
            calls = []

            async def query(model, messages, **kwargs):
                calls.append((model, copy.deepcopy(messages), kwargs["trace_context"]))
                return {"content": "{}"}

            with patch.object(council, "query_model", query):
                asyncio.run(
                    council.stage_negative_evaluations(
                        response.user_query,
                        self.fixture.policy.negative_prompt,
                        active,
                        labels,
                    )
                )
            for (model, messages, context), request in zip(
                calls, saved.requests, strict=True
            ):
                self.assertEqual(model, request.model)
                self.assertEqual(messages, request.payload()["messages"])
                self.assertEqual(
                    context,
                    {
                        "kind": "gap_review_full",
                        "self_label": request.self_label,
                        "peer_labels": list(request.peer_labels),
                    },
                )

    def test_extracted_prompt_preserves_prechange_exact_bytes(self):
        # Golden derived from the original inline prompt before extraction.
        text = council.build_negative_evaluation_prompt(
            "Question ☃",
            "  Concerns\nПример  ",
            [
                {"model": "one", "response": "  First\nanswer  "},
                {"model": "two", "response": "Second"},
            ],
            ["Response A", "Response B"],
            0,
        )
        self.assertEqual(
            hashlib.sha256(text.encode()).hexdigest(),
            "c259a04b90e2c896d2236038c87df55f2c8b9f35f44a19c9f4a50de159ada85c",
        )

    def test_idempotent_concurrent_binding_and_detached_payloads(self):
        self.complete()
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda _: self.bind(), range(8)))
        self.assertTrue(all(value == values[0] for value in values))
        original = self.path.read_bytes()
        values[0].label_to_model.clear()
        values[0].requests[0].payload()["messages"].clear()
        with patch.object(run_admission, "_write", side_effect=AssertionError("write")):
            self.assertEqual(self.bind(), values[1])
        self.assertEqual(original, self.path.read_bytes())

    def test_historical_settings_and_cancellation_reads_but_no_rebinding(self):
        self.complete()
        saved = self.bind()
        with (
            patch.object(config, "NEGATIVE_MODE_ENABLED", False),
            patch.object(config, "OPENROUTER_API_URL", "https://changed.invalid"),
        ):
            self.assertEqual(self.bind(), saved)
            self.assertEqual(
                run_inspection.inspect(self.run)["negative_inputs"],
                negative.summary(saved),
            )
        run_cancellation.request(self.run)
        self.assertEqual(
            run_inspection.inspect(self.run)["negative_inputs"], negative.summary(saved)
        )
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()

    def test_invalid_revision_unfinished_missing_policy_and_deletion(self):
        self.complete()
        for revision in (True, -1, "6"):
            with self.assertRaises(ValueError):
                self.bind(revision)
        with self.assertRaises(run_interaction.PromptConflict):
            self.bind(3)
        record = json.loads(self.path.read_text())
        record.pop("response_review_policy")
        self.path.write_text(json.dumps(record))
        with self.assertRaises(negative.NegativeInputConflict):
            self.bind()
        run_admission.tombstone_conversation(f"cli-{self.run}")
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.bind()

    def test_budget_boundary_and_overflow_fail_without_reduction(self):
        self.complete()
        record, interaction = run_interaction._load(self.run)
        budgets = council_model_budgets.validate_saved(record, interaction)
        minimum = min(b.input_budget for b in budgets.models)
        original = model_context.token_bound

        def count(messages, extra=0):
            return (
                minimum + extra
                if messages[0]["content"].startswith("You will act")
                else original(messages)
            )

        with patch.object(model_context, "token_bound", side_effect=count):
            self.assertTrue(
                all(
                    r.input_token_bound == minimum
                    for r in negative._derive(record, interaction).requests
                )
            )
        before = self.path.read_bytes()
        with patch.object(
            model_context, "token_bound", side_effect=lambda m: count(m, 1)
        ):
            with self.assertRaises(negative.NegativeInputConflict):
                self.bind()
        self.assertEqual(before, self.path.read_bytes())

    def test_tampered_requests_and_causal_sources_fail_closed(self):
        self.complete({"exclude_responses": ["provider/first"]})
        self.bind()
        original = self.path.read_bytes()
        mutations = [
            lambda r: r["negative_inputs"].update(version=True),
            lambda r: r["negative_inputs"].update(source_revision=3),
            lambda r: r["negative_inputs"].update(endpoint="https://other.invalid"),
            lambda r: r["negative_inputs"].update(negative_prompt_source="review"),
            lambda r: r["negative_inputs"].update(active_models=[]),
            lambda r: r["negative_inputs"]["requests"][0].update(
                self_label="Response B"
            ),
            lambda r: r["negative_inputs"]["requests"][0].update(
                peer_labels=["Response A"]
            ),
            lambda r: r["negative_inputs"]["requests"][0].update(payload_json="{}"),
            lambda r: r["negative_inputs"]["requests"][0].update(input_token_bound=1),
            lambda r: r["negative_inputs"].update(unknown=True),
            lambda r: r["stage1_execution"]["operations"][0]["result"].update(
                content="excluded but changed"
            ),
            lambda r: r.pop("response_review_policy"),
            lambda r: r.update(phase="initializing"),
        ]
        for mutate in mutations:
            self.path.write_bytes(original)
            record = json.loads(original)
            mutate(record)
            self.path.write_text(json.dumps(record))
            before = self.path.read_bytes()
            with self.assertRaises(run_admission.AdmissionUnavailable):
                run_inspection.inspect(self.run)
            with self.assertRaises(run_admission.AdmissionUnavailable):
                self.bind()
            self.assertEqual(before, self.path.read_bytes())

    def test_failed_write_and_lost_acknowledgment(self):
        self.complete()
        before = self.path.read_bytes()
        with patch.object(run_admission, "_write", side_effect=OSError("before write")):
            with self.assertRaises(OSError):
                self.bind()
        self.assertEqual(before, self.path.read_bytes())
        writer = run_admission._write

        def lost(record):
            writer(record)
            raise OSError("lost acknowledgment")

        with patch.object(run_admission, "_write", side_effect=lost):
            with self.assertRaises(OSError):
                self.bind()
        retained = self.path.read_bytes()
        with patch.object(
            run_admission, "_write", side_effect=AssertionError("duplicate write")
        ):
            self.bind()
        self.assertEqual(retained, self.path.read_bytes())

    def test_unconsumed_review_is_rejected_without_writes(self):
        self.fixture.fixture.prepare(negative=True, requested="Exact concern")
        self.run = self.fixture.fixture.run
        before = run_admission._path(self.run).read_bytes()
        with self.assertRaises(negative.NegativeInputConflict):
            self.bind(3)
        self.assertEqual(before, run_admission._path(self.run).read_bytes())

    def test_fresh_process_restores_without_current_config_or_regeneration(self):
        self.complete(skipped=True)
        saved = self.bind().model_dump(mode="json")
        before = self.path.read_bytes()
        script = """import json,sys
from unittest.mock import patch
from backend import config,storage,run_negative_inputs,run_response_review
storage.DATA_DIR=sys.argv[1]
config.COUNCIL_MODELS=[]
config.NEGATIVE_MODE_ENABLED=False
with patch.object(run_response_review,'get_response_review_prompt',side_effect=AssertionError('regeneration')):
 print(json.dumps(run_negative_inputs.bind(sys.argv[2],expected_revision=6).model_dump(mode='json')))
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
        self.assertEqual(before, self.path.read_bytes())
