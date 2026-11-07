"""Offline downstream policy from real complete Stage 1 and human receipts."""

import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from unittest.mock import patch
from uuid import uuid4

import test_run_stage1_continuation_inputs as fixtures
from test_durable_response_review import STOP

from backend import (
    config,
    durable_response_review,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    storage,
)
from backend import response_review_policy as policy
from backend.consultation_contract import AnswerRequest


class ResponseReviewPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = None

    def prepare(self, *, negative=False, requested=None):
        # Compose existing durable fixtures, without inheriting their test cases.
        if self.fixture is not None:
            self.fixture.doCleanups()
        self.fixture = fixtures.FirstContinuationInputTests()
        self.addCleanup(self.fixture.doCleanups)
        prepare = run_admission.prepare

        def with_prompt(run, request):
            return prepare(
                run, request.model_copy(update={"negative_prompt": requested})
            )

        with patch.object(run_admission, "prepare", side_effect=with_prompt):
            self.fixture.setUp()
        setting = patch.object(config, "NEGATIVE_MODE_ENABLED", negative)
        setting.start()
        self.fixture.addCleanup(setting.stop)
        self.fixture.initial()
        self.run, self.models = self.fixture.run_id, self.fixture.models

    def complete(
        self, responses=None, *, skipped=False, negative=False, requested=None
    ):
        self.prepare(negative=negative, requested=requested)
        for model in self.models:
            self.fixture.execute(model, STOP)
        durable_response_review.advance(self.run, expected_revision=3)
        self.answer(responses, skipped=skipped)
        return durable_response_review.advance(self.run, expected_revision=5)

    def answer(self, responses=None, *, skipped=False):
        prompt = run_inspection.inspect(self.run)["current_prompt"]["instance_id"]
        return run_interaction.answer(
            self.run, prompt, AnswerRequest(responses=responses or {}, skipped=skipped)
        )

    def bind(self, revision=6):
        return policy.bind(self.run, expected_revision=revision)

    def files(self):
        return self.fixture.fixture.files()

    def rewrite(self, mutate):
        path = run_admission._path(self.run)
        record = json.loads(path.read_text())
        mutate(record)
        path.write_text(json.dumps(record))

    def test_exclusion_preserves_source_and_relabels_surviving_answers(self):
        before = self.complete({"exclude_responses": ["provider/first"]})
        saved = self.bind()
        self.assertEqual(saved.excluded_models, (self.models[0],))
        self.assertEqual(saved.active_models, (self.models[1],))
        self.assertEqual(saved.label_to_model, {"Response A": self.models[1]})
        self.assertEqual(
            str(saved.source_instance_id),
            before["interaction"]["prompts"][-1]["instance"]["instance_id"],
        )
        self.assertEqual(saved.source_revision, 6)
        self.assertEqual(
            saved.stage1_source_sha256, before["response_review"]["source_sha256"]
        )
        after = run_inspection.inspect(self.run)
        for key in (
            "interaction",
            "stage1",
            "response_review",
            "pre_query_policy",
            "internal_phase",
        ):
            self.assertEqual(after[key], before[key])
        self.assertNotEqual(after["cursor"], before["cursor"])
        self.assertEqual(after["response_review_policy"], saved.model_dump(mode="json"))
        self.assertEqual(saved.next_stage, "stage2")

    def test_all_excluded_is_explicitly_blocked_not_silently_restored(self):
        self.complete(
            {"exclude_responses": ["provider/second", "provider/first"]}, negative=True
        )
        saved = self.bind()
        self.assertEqual(saved.excluded_models, tuple(self.models))
        self.assertEqual(saved.active_models, ())
        self.assertEqual(saved.label_to_model, {})
        self.assertEqual(saved.next_stage, "blocked_no_active_responses")
        self.assertEqual(
            run_inspection.inspect(self.run)["response_review"]["answer"]["responses"][
                "exclude_responses"
            ],
            list(reversed(self.models)),
        )

    def test_skip_omission_and_empty_values_preserve_raw_answer(self):
        for responses, skipped in (
            ({}, False),
            ({}, True),
            ({"exclude_responses": [], "gap_concerns": ""}, False),
        ):
            with self.subTest(responses=responses, skipped=skipped):
                before = self.complete(responses, skipped=skipped)
                saved = self.bind()
                self.assertEqual(saved.active_models, tuple(self.models))
                self.assertEqual(saved.excluded_models, ())
                self.assertIsNone(saved.negative_prompt)
                self.assertEqual(saved.negative_prompt_source, "disabled")
                self.assertEqual(
                    run_inspection.inspect(self.run)["response_review"],
                    before["response_review"],
                )

    def test_negative_prompt_precedence_matches_legacy_truthiness(self):
        # Compare the graph's exact `request or gap or generate` semantics,
        # including blank/whitespace text. No provider or generator is invoked.
        for requested, concern, skipped in product(
            (None, "", "  ", " Request Пример\n"),
            (None, "", "  ", " Concern ☃\n"),
            (False, True),
        ):
            responses = {} if concern is None or skipped else {"gap_concerns": concern}
            with self.subTest(requested=requested, concern=concern, skipped=skipped):
                self.complete(
                    responses, skipped=skipped, negative=True, requested=requested
                )
                saved = self.bind()
                effective_concern = None if skipped else concern
                expected = requested or effective_concern or None
                source = (
                    "request"
                    if requested
                    else "review"
                    if effective_concern
                    else "generation_required"
                )
                self.assertEqual(saved.negative_prompt, expected)
                self.assertEqual(saved.negative_prompt_source, source)
                self.assertEqual(saved.next_stage, "negative")
                self.assertTrue(saved.negative_enabled)
                if expected:
                    self.assertNotIn(repr(expected), repr(saved))

    def test_gap_concerns_and_request_do_not_enable_negative_stage(self):
        self.complete({"gap_concerns": "Review concern"}, requested="Request concern")
        saved = self.bind()
        self.assertFalse(saved.negative_enabled)
        self.assertEqual(saved.next_stage, "stage2")
        self.assertEqual(saved.negative_prompt_source, "disabled")
        self.assertIsNone(saved.negative_prompt)

    def test_current_configuration_and_prompt_defaults_do_not_rewrite_policy(self):
        self.complete(negative=True)
        with (
            patch.object(config, "NEGATIVE_MODE_ENABLED", False),
            patch.object(config, "COUNCIL_MODELS", ["new/model"]),
        ):
            saved = self.bind()
        self.assertTrue(saved.negative_enabled)
        self.assertEqual(saved.active_models, tuple(self.models))
        self.assertEqual(saved.negative_prompt_source, "generation_required")
        self.rewrite(
            lambda r: r["interaction"]["prompts"][-1]["instance"]["prompt"]["fields"][
                0
            ].__setitem__("default", [self.models[0]])
        )
        self.assertEqual(self.bind(), saved)

    def test_json_mapping_order_is_not_the_source_roster_order(self):
        self.complete()
        before = self.bind()

        def reorder(record):
            entry = record["interaction"]["prompts"][-1]
            for key in ("continuation", "next_continuation"):
                mapping = entry[key]["state"]["label_to_model"]
                entry[key]["state"]["label_to_model"] = dict(
                    reversed(list(mapping.items()))
                )

        self.rewrite(reorder)
        self.assertEqual(self.bind(), before)
        self.assertEqual(self.bind().active_models, tuple(self.models))

    def test_unfinished_stale_and_invalid_revisions_fail_without_writing(self):
        self.prepare()
        for revision in (True, -1, "3"):
            with self.assertRaises(ValueError):
                self.bind(revision)
        for revision in (0, 3):
            with self.assertRaises(run_interaction.PromptConflict):
                self.bind(revision)
        for model in self.models:
            self.fixture.execute(model, STOP)
        durable_response_review.advance(self.run, expected_revision=3)
        before = self.files()
        with self.assertRaises(run_interaction.PromptConflict):
            self.bind(4)
        self.assertEqual(self.files(), before)
        self.answer()
        with self.assertRaises(run_interaction.PromptConflict):
            self.bind(5)
        durable_response_review.advance(self.run, expected_revision=5)
        before = self.files()
        with self.assertRaises(run_interaction.PromptConflict):
            self.bind(5)
        self.assertEqual(self.files(), before)

    def test_racing_binds_write_once_and_return_detached_values(self):
        self.complete()
        with patch.object(
            run_admission, "_write", wraps=run_admission._write
        ) as writer:
            with ThreadPoolExecutor(max_workers=6) as pool:
                values = list(pool.map(lambda _: self.bind(), range(18)))
            self.assertEqual(writer.call_count, 1)
        self.assertTrue(all(value == values[0] for value in values))
        before = self.files()
        values[0].label_to_model.clear()
        snapshot = run_inspection.inspect(self.run)
        snapshot["response_review_policy"]["active_models"].clear()
        self.assertEqual(self.bind(), values[1])
        self.assertEqual(self.files(), before)
        with self.assertRaises(ValueError):
            values[1].next_stage = "negative"

    def test_corrupted_effective_policy_fails_closed_without_repair(self):
        self.complete({"gap_concerns": "Exact concern"}, negative=True)
        self.bind()
        path = run_admission._path(self.run)
        original = path.read_bytes()
        mutations = [
            ("version", True),
            ("version", 2),
            ("source_instance_id", str(uuid4())),
            ("source_revision", 3),
            ("stage1_source_sha256", "0" * 64),
            ("excluded_models", [self.models[0]]),
            ("active_models", list(reversed(self.models))),
            ("label_to_model", {}),
            ("negative_enabled", False),
            ("negative_prompt_source", "request"),
            ("negative_prompt", "Changed"),
            ("next_stage", "stage2"),
            ("unknown", True),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                path.write_bytes(original)
                self.rewrite(
                    lambda r: r["response_review_policy"].__setitem__(key, value)
                )
                before = self.files()
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    run_inspection.inspect(self.run)
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    self.bind()
                self.assertEqual(self.files(), before)

    def test_policy_cannot_outlive_missing_or_changed_review_source(self):
        self.complete()
        self.bind()
        path = run_admission._path(self.run)
        original = path.read_bytes()
        for mutate in (
            lambda r: r.pop("interaction"),
            lambda r: r.pop("council_selection"),
            lambda r: r["interaction"]["prompts"][-1]["continuation"][
                "state"
            ].__setitem__("source_sha256", "0" * 64),
            lambda r: r.__setitem__("phase", "initializing"),
        ):
            path.write_bytes(original)
            self.rewrite(mutate)
            before = self.files()
            with self.assertRaises(run_admission.AdmissionUnavailable):
                run_inspection.inspect(self.run)
            self.assertEqual(self.files(), before)

    def test_failed_write_and_lost_acknowledgment_recover_without_second_write(self):
        self.complete()
        before = self.files()
        with patch.object(run_admission, "_write", side_effect=OSError("before write")):
            with self.assertRaises(OSError):
                self.bind()
        self.assertEqual(self.files(), before)
        writer = run_admission._write

        def lost_ack(record):
            writer(record)
            raise OSError("lost acknowledgment")

        with patch.object(run_admission, "_write", side_effect=lost_ack):
            with self.assertRaises(OSError):
                self.bind()
        before = self.files()
        with patch.object(
            run_admission, "_write", side_effect=AssertionError("duplicate write")
        ):
            saved = self.bind()
        self.assertEqual(
            saved.model_dump(mode="json"),
            run_inspection.inspect(self.run)["response_review_policy"],
        )
        self.assertEqual(self.files(), before)

    def test_fresh_process_restores_policy_without_settings_or_model_work(self):
        self.complete(
            {"gap_concerns": " Private synthetic concern Пример\n"}, negative=True
        )
        saved = self.bind().model_dump(mode="json")
        before = self.files()
        script = """import json,sys
from backend import config,response_review_policy,storage
storage.DATA_DIR=sys.argv[1]
config.NEGATIVE_MODE_ENABLED=False
config.COUNCIL_MODELS=[]
print(json.dumps(response_review_policy.bind(sys.argv[2],expected_revision=6).model_dump(mode='json')))
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

    def test_cancellation_keeps_readable_evidence_and_blocks_rebinding(self):
        self.complete()
        saved = self.bind().model_dump(mode="json")
        run_cancellation.request(self.run)
        snapshot = run_inspection.inspect(self.run)
        self.assertEqual(snapshot["internal_phase"], "cancelled")
        self.assertEqual(snapshot["response_review_policy"], saved)
        before = self.files()
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()
        self.assertEqual(self.files(), before)

    def test_deletion_removes_policy_and_prevents_recreation(self):
        self.complete()
        self.bind()
        storage.delete_conversation(f"cli-{self.run}")
        record = json.loads(run_admission._path(self.run).read_text())
        self.assertNotIn("response_review_policy", record)
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.bind()
