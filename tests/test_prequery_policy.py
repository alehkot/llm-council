"""Offline response-policy snapshots using real run receipts and legacy semantics."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import (
    durable_prequery,
    durable_refinement,
    prequery_policy,
    refinement_execution,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    storage,
)
from backend.consultation_contract import AnswerRequest, CreateRunRequest
from backend.interactive import InteractiveConfig, PromptResponse


class ResponsePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.data = str(self.directory / "conversations")
        mocked = patch.object(storage, "DATA_DIR", self.data)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.run = self.prepare()

    def prepare(self):
        run = str(uuid4())
        run_admission.prepare(
            run,
            CreateRunRequest(
                content="Question — вопрос",
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        return run

    def complete(self, values=None, skipped=False, run=None):
        run = run or self.run
        waiting = durable_prequery.advance(run, expected_revision=0)
        run_interaction.answer(
            run,
            waiting["current_prompt"]["instance_id"],
            AnswerRequest(responses=values or {}, skipped=skipped),
        )
        return durable_prequery.advance(run, expected_revision=2)

    def bind(self, run=None):
        return prequery_policy.bind(run or self.run, expected_revision=3)

    def files(self):
        return {
            str(p): p.read_bytes() for p in self.directory.rglob("*") if p.is_file()
        }

    def rewrite(self, mutate):
        path = run_admission._path(self.run)
        record = json.loads(path.read_text())
        mutate(record)
        path.write_text(json.dumps(record))

    def test_omission_defaults_are_effective_not_accepted_values(self):
        before = self.complete({"domain": "coding"})
        policy = self.bind()
        self.assertEqual(
            (policy.domain, policy.style, policy.priority),
            ("coding", "detailed", "accuracy"),
        )
        self.assertEqual(policy.user_query, "Question — вопрос")
        self.assertEqual(policy.source_revision, 3)
        after = run_inspection.inspect(self.run)
        self.assertEqual(before["interaction"], after["interaction"])
        self.assertEqual(before["pre_query"], after["pre_query"])
        self.assertNotEqual(before["cursor"], after["cursor"])
        self.assertEqual(
            after["pre_query"]["answer"]["responses"], {"domain": "coding"}
        )
        self.assertEqual(after["pre_query_policy"], policy.model_dump(mode="json"))
        self.assertEqual(after["internal_phase"], "pre_query_complete")

    def test_explicit_skip_has_no_effective_defaults_or_modifier(self):
        self.complete(skipped=True)
        policy = self.bind()
        self.assertIsNone(policy.domain)
        self.assertIsNone(policy.style)
        self.assertIsNone(policy.priority)
        self.assertIsNone(policy.system_prompt_modifier)
        run = self.prepare()
        self.complete(run=run)
        self.assertIn("factual accuracy", self.bind(run).system_prompt_modifier)

    def test_all_choices_and_optional_omissions_match_legacy(self):
        # All 8 * 4 * 5 combinations, including absent optional fields.
        for domain, style, priority in product(
            [None, *durable_prequery._OPTIONS["domain"]],
            [None, *durable_prequery._OPTIONS["style"]],
            [None, *durable_prequery._OPTIONS["priority"]],
        ):
            values = {
                k: v
                for k, v in zip(
                    ("domain", "style", "priority"), (domain, style, priority)
                )
                if v is not None
            }
            run = self.prepare()
            self.complete(values, run=run)
            policy = self.bind(run)
            legacy = InteractiveConfig.from_responses(
                {"pre_query_config": PromptResponse("pre_query_config", values)}
            )
            with self.subTest(values=values):
                self.assertEqual(
                    policy.system_prompt_modifier,
                    legacy.get_system_prompt_modifier() or None,
                )
                self.assertEqual(
                    (policy.domain, policy.style, policy.priority),
                    (legacy.domain, legacy.style, legacy.priority),
                )

    def test_concurrent_bind_is_idempotent_and_reads_are_detached(self):
        self.complete()
        with ThreadPoolExecutor(max_workers=8) as pool:
            policies = list(pool.map(lambda _: self.bind(), range(24)))
        self.assertTrue(all(p == policies[0] for p in policies))
        before = self.files()
        self.bind()
        snapshot = run_inspection.inspect(self.run)
        snapshot["pre_query_policy"]["priority"] = "clarity"
        self.assertEqual(
            run_inspection.inspect(self.run)["pre_query_policy"]["priority"], "accuracy"
        )
        self.assertEqual(before, self.files())
        with self.assertRaises(ValueError):
            policies[0].priority = "clarity"

    def test_unfinished_stale_and_invalid_revisions_cannot_bind(self):
        for revision in (True, -1, "0"):
            with self.assertRaises(ValueError):
                prequery_policy.bind(self.run, expected_revision=revision)
        with self.assertRaises(run_interaction.PromptConflict):
            prequery_policy.bind(self.run, expected_revision=0)
        durable_prequery.advance(self.run, expected_revision=0)
        with self.assertRaises(run_interaction.PromptConflict):
            prequery_policy.bind(self.run, expected_revision=1)
        prompt = run_inspection.inspect(self.run)["current_prompt"]["instance_id"]
        run_interaction.answer(self.run, prompt, AnswerRequest(responses={}))
        with self.assertRaises(run_interaction.PromptConflict):
            prequery_policy.bind(self.run, expected_revision=2)
        durable_prequery.advance(self.run, expected_revision=2)
        with self.assertRaises(run_interaction.PromptConflict):
            prequery_policy.bind(self.run, expected_revision=2)

    def test_corrupted_policy_fails_closed_without_rewrite(self):
        self.complete()
        self.bind()
        path = run_admission._path(self.run)
        original = path.read_bytes()
        for key, value in [
            ("version", True),
            ("version", 2),
            ("source_revision", 2),
            ("source_instance_id", str(uuid4())),
            ("user_query", "wrong"),
            ("style", "concise"),
            ("system_prompt_modifier", "wrong"),
        ]:
            path.write_bytes(original)
            self.rewrite(lambda r: r["pre_query_policy"].__setitem__(key, value))
            before = self.files()
            with self.subTest(key=key, value=value):
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    run_inspection.inspect(self.run)
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    self.bind()
                self.assertEqual(before, self.files())

    def test_policy_cannot_be_attached_without_source(self):
        self.complete()
        self.bind()
        self.rewrite(lambda r: r.pop("interaction"))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_inspection.inspect(self.run)

    def test_semantics_change_is_not_silently_rebound(self):
        self.complete()
        self.bind()
        before = self.files()
        with patch.object(
            InteractiveConfig, "get_system_prompt_modifier", return_value="changed"
        ):
            with self.assertRaises(run_admission.AdmissionUnavailable):
                self.bind()
        self.assertEqual(before, self.files())

    def test_before_and_after_write_failures_preserve_idempotency(self):
        self.complete()
        before = self.files()
        with patch.object(run_admission, "_write", side_effect=OSError("before")):
            with self.assertRaises(OSError):
                self.bind()
        self.assertEqual(before, self.files())
        writer = run_admission._write

        def after(record):
            writer(record)
            raise OSError("lost acknowledgment")

        with patch.object(run_admission, "_write", side_effect=after):
            with self.assertRaises(OSError):
                self.bind()
        saved = self.files()
        self.bind()
        self.assertEqual(saved, self.files())

    def test_fresh_process_restores_exact_policy(self):
        self.complete({"style": "comprehensive", "priority": "clarity"})
        expected = self.bind().model_dump(mode="json")
        code = """import json,sys
from backend import storage,run_inspection
storage.DATA_DIR=sys.argv[1]
print(json.dumps(run_inspection.inspect(sys.argv[2])["pre_query_policy"]))
"""
        before = self.files()
        output = subprocess.check_output(
            [sys.executable, "-c", code, self.data, self.run], timeout=20, text=True
        )
        self.assertEqual(json.loads(output), expected)
        self.assertEqual(before, self.files())

    def test_cancel_preserves_evidence_but_prevents_bind(self):
        self.complete()
        expected = self.bind().model_dump(mode="json")
        run_cancellation.request(self.run)
        snapshot = run_inspection.inspect(self.run)
        self.assertEqual(snapshot["pre_query_policy"], expected)
        self.assertEqual(snapshot["internal_phase"], "cancelled")
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()

    def test_deletion_erases_policy_and_prevents_recreation(self):
        self.complete()
        self.bind()
        storage.delete_conversation(f"cli-{self.run}")
        record = json.loads(run_admission._path(self.run).read_text())
        self.assertNotIn("pre_query_policy", record)
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.bind()

    def test_refined_query_is_exact_and_is_not_recomputed(self):
        run = str(uuid4())
        run_admission.prepare(
            run,
            CreateRunRequest(
                content="Original",
                refine=True,
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )

        async def provider(**kwargs):
            return {
                "intent": "Scope",
                "clarifying_questions": [],
                "refined_prompt": "Exact refined — вопрос",
            }

        asyncio.run(
            refinement_execution.suggest(
                run, provider, provider_key="offline-v1", expected_revision=0
            )
        )
        prompt = run_inspection.inspect(run)["current_prompt"]["instance_id"]
        run_interaction.answer(
            run, prompt, AnswerRequest(responses={"refinement_action": "use_refined"})
        )
        durable_refinement.resume_answer(run, prompt)
        revision = run_inspection.inspect(run)["interaction"]["revision"]
        waiting = durable_prequery.advance(run, expected_revision=revision)
        run_interaction.answer(
            run,
            waiting["current_prompt"]["instance_id"],
            AnswerRequest(responses={"style": "concise"}),
        )
        done = durable_prequery.advance(run, expected_revision=revision + 2)
        policy = prequery_policy.bind(
            run, expected_revision=done["interaction"]["revision"]
        )
        self.assertEqual(policy.user_query, "Exact refined — вопрос")
        self.assertEqual(
            policy.source_instance_id, waiting["current_prompt"]["instance_id"]
        )

    def test_unprepared_policy_is_rejected(self):
        self.rewrite(lambda r: r.update(phase="initializing", pre_query_policy={}))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_inspection.inspect(self.run)

    def test_cancellation_before_binding_saves_no_policy(self):
        self.complete()
        run_cancellation.request(self.run)
        before = self.files()
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()
        self.assertEqual(before, self.files())
        self.assertIsNone(run_inspection.inspect(self.run)["pre_query_policy"])

    def test_missing_policy_version_is_not_defaulted(self):
        self.complete()
        self.bind()
        self.rewrite(lambda r: r["pre_query_policy"].pop("version"))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_inspection.inspect(self.run)
