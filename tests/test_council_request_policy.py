"""Offline request-policy provenance using real durable receipts and boundaries."""

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
    config,
    council_request_policy,
    council_selection,
    durable_prequery,
    prequery_policy,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    storage,
)
from backend.consultation_contract import AnswerRequest, CreateRunRequest

SETTINGS = {
    "OPENROUTER_API_URL": ("endpoint", "https://provider.example/chat"),
    "OPENROUTER_MAX_TOKENS": ("max_tokens", 1234),
    "OPENROUTER_MAX_CONTEXT_CALLS": ("max_context_calls", 5),
    "OPENROUTER_MAX_CONTINUATIONS": ("max_continuations", 3),
    "OPENROUTER_MAX_RETRIES": ("max_retries", 2),
    "OPENROUTER_RETRY_BASE_SECONDS": ("retry_base_seconds", 0.75),
    "OPENROUTER_RETRY_MAX_SECONDS": ("retry_max_seconds", 9.0),
    "OPENROUTER_RETRY_JITTER": ("retry_jitter", 0.25),
}


class CouncilRequestPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = str(self.root / "conversations")
        for target, key, value in (
            (storage, "DATA_DIR", self.data),
            (config, "MODEL_BACKEND", "openrouter"),
            (config, "COUNCIL_MODELS", ["provider/first", "provider/second"]),
            (config, "NEGATIVE_MODE_ENABLED", False),
            (config, "DELIBERATION_ENABLED", True),
            (config, "PAIRWISE_RANKING_ENABLED", False),
            *((config, key, value) for key, (_, value) in SETTINGS.items()),
        ):
            p = patch.object(target, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.run = str(uuid4())
        run_admission.prepare(
            self.run,
            CreateRunRequest(
                content="Question — вопрос",
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )

    def complete(self, selection=True):
        prompt = durable_prequery.advance(self.run, expected_revision=0)[
            "current_prompt"
        ]
        run_interaction.answer(
            self.run, prompt["instance_id"], AnswerRequest(responses={})
        )
        durable_prequery.advance(self.run, expected_revision=2)
        prequery_policy.bind(self.run, expected_revision=3)
        if selection:
            council_selection.bind(self.run, expected_revision=3)

    def bind(self, revision=3):
        return council_request_policy.bind(self.run, expected_revision=revision)

    def files(self):
        return {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def rewrite(self, modify):
        path = run_admission._path(self.run)
        record = json.loads(path.read_text())
        modify(record)
        path.write_text(json.dumps(record))

    def test_exact_loaded_configuration_and_source_are_retained(self):
        self.complete()
        before = run_inspection.inspect(self.run)
        policy = self.bind()
        for _, (key, value) in SETTINGS.items():
            self.assertEqual(getattr(policy, key), value)
        selected = council_selection.CouncilSelection.model_validate(
            before["council_selection"]
        )
        self.assertEqual(
            policy.council_selection_sha256,
            council_request_policy._selection_identity(selected),
        )
        self.assertEqual(policy.source_revision, 3)
        after = run_inspection.inspect(self.run)
        self.assertEqual(
            after["council_request_policy"], policy.model_dump(mode="json")
        )
        self.assertNotEqual(before["cursor"], after["cursor"])
        self.assertEqual(after["internal_phase"], "pre_query_complete")
        for key in ("interaction", "pre_query_policy", "council_selection", "request"):
            self.assertEqual(before[key], after[key])

    def test_frozen_detached_and_credentials_are_never_captured(self):
        self.complete()
        with (
            patch.object(
                config, "OPENROUTER_API_KEY", "synthetic-private-provider-key"
            ),
            patch.object(config, "API_AUTH_KEY", "synthetic-private-local-key"),
        ):
            policy = self.bind()
        with self.assertRaises(ValueError):
            policy.max_retries = 9
        dumped = policy.model_dump(mode="json")
        dumped["max_retries"] = 99
        self.assertEqual(policy.max_retries, 2)
        self.assertNotIn("synthetic-private", run_admission._path(self.run).read_text())
        with patch.object(config, "OPENROUTER_API_KEY", "rotated-provider-key"):
            self.assertEqual(self.bind(), policy)

    def test_idempotent_binds_and_reads_are_byte_pure(self):
        self.complete()
        policy = self.bind()
        before = self.files()
        self.assertEqual(self.bind(), policy)
        run_inspection.inspect(self.run)
        self.assertEqual(before, self.files())

    def test_each_request_setting_drift_conflicts_but_historical_read_survives(self):
        self.complete()
        policy = self.bind().model_dump(mode="json")
        before = self.files()
        for key, (_, value) in SETTINGS.items():
            different = (
                value + 1 if isinstance(value, (float, int)) else value + "/other"
            )
            with self.subTest(key=key), patch.object(config, key, different):
                self.assertEqual(
                    run_inspection.inspect(self.run)["council_request_policy"], policy
                )
                with self.assertRaises(council_request_policy.RequestPolicyConflict):
                    self.bind()
                self.assertEqual(before, self.files())

    def test_selection_drift_cannot_bind_or_rebind_request_policy(self):
        self.complete()
        for previously_bound in (False, True):
            if previously_bound:
                self.bind()
            for key, value in (
                ("COUNCIL_MODELS", ["provider/second", "provider/first"]),
                ("NEGATIVE_MODE_ENABLED", True),
                ("DELIBERATION_ENABLED", False),
                ("PAIRWISE_RANKING_ENABLED", True),
                ("MODEL_BACKEND", "acp"),
                ("COUNCIL_MODELS", []),
            ):
                before = self.files()
                with (
                    self.subTest(bound=previously_bound, key=key),
                    patch.object(config, key, value),
                ):
                    with self.assertRaises(
                        council_request_policy.RequestPolicyConflict
                    ):
                        self.bind()
                    run_inspection.inspect(self.run)
                    self.assertEqual(before, self.files())

    def test_invalid_endpoint_is_rejected_before_writes(self):
        self.complete()
        before = self.files()
        for value in (
            "",
            "http://provider.example/chat",
            "https:///chat",
            "https://user:secret@provider.example/chat",
            "https://user@provider.example/chat",
            "https://provider.example/chat?key=secret",
            "https://provider.example/chat#secret",
            "https://provider.example:70000/chat",
            "https://[broken/chat",
            " https://provider.example/chat",
            "https://provider.example/ch\nat",
            "https://provider.example/\x00chat",
            "https://provider.example/\x7fchat",
            123,
            None,
        ):
            with (
                self.subTest(endpoint=value),
                patch.object(config, "OPENROUTER_API_URL", value),
            ):
                with self.assertRaises(council_request_policy.RequestPolicyConflict):
                    self.bind()
                self.assertEqual(before, self.files())

    def test_invalid_numeric_settings_fail_closed(self):
        self.complete()
        before = self.files()
        for key, values in (
            ("OPENROUTER_MAX_TOKENS", [0, -1, True, "100", 1.5]),
            ("OPENROUTER_MAX_CONTEXT_CALLS", [-1, True, "1", 1.5]),
            ("OPENROUTER_MAX_CONTINUATIONS", [-1, True, "1", 1.5]),
            ("OPENROUTER_MAX_RETRIES", [-1, True, "1", 1.5]),
            (
                "OPENROUTER_RETRY_BASE_SECONDS",
                [0, -1, True, "1", 10, float("nan"), float("inf")],
            ),
            (
                "OPENROUTER_RETRY_MAX_SECONDS",
                [0, -1, True, "1", 0.5, float("nan"), float("inf")],
            ),
            ("OPENROUTER_RETRY_JITTER", [-1, True, "1", float("nan"), float("inf")]),
        ):
            for value in values:
                with (
                    self.subTest(key=key, value=value),
                    patch.object(config, key, value),
                ):
                    with self.assertRaises(
                        council_request_policy.RequestPolicyConflict
                    ):
                        self.bind()
                    self.assertEqual(before, self.files())

    def test_zero_extra_call_limits_and_jitter_are_retained(self):
        self.complete()
        with (
            patch.object(config, "OPENROUTER_MAX_CONTEXT_CALLS", 0),
            patch.object(config, "OPENROUTER_MAX_CONTINUATIONS", 0),
            patch.object(config, "OPENROUTER_MAX_RETRIES", 0),
            patch.object(config, "OPENROUTER_RETRY_JITTER", 0.0),
        ):
            policy = self.bind()
            self.assertEqual(policy.max_context_calls, 0)
            self.assertEqual(policy.max_continuations, 0)
            self.assertEqual(policy.max_retries, 0)
            self.assertEqual(policy.retry_jitter, 0.0)
            self.assertEqual(self.bind(), policy)

    def test_source_must_be_explicitly_bound_and_complete(self):
        before = self.files()
        with self.assertRaises(council_request_policy.RequestPolicyConflict):
            self.bind(revision=0)
        self.assertEqual(before, self.files())
        self.complete(selection=False)
        before = self.files()
        with self.assertRaises(council_request_policy.RequestPolicyConflict):
            self.bind()
        self.assertEqual(before, self.files())
        self.assertIsNone(run_inspection.inspect(self.run)["council_selection"])

    def test_unknown_run_stale_and_malformed_revisions_do_not_bind(self):
        with self.assertRaises(run_admission.AdmissionUnavailable):
            council_request_policy.bind(str(uuid4()), expected_revision=3)
        self.complete()
        self.bind()
        before = self.files()
        for revision in (0, 2, 4):
            with self.assertRaises(run_interaction.PromptConflict):
                self.bind(revision)
        for revision in (True, "3", -1):
            with self.assertRaises(ValueError):
                self.bind(revision)
        self.assertEqual(before, self.files())

    def test_parallel_duplicate_binds_have_one_write(self):
        self.complete()
        with patch.object(
            run_admission, "_write", wraps=run_admission._write
        ) as writer:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: self.bind(), range(24)))
        self.assertEqual(writer.call_count, 1)
        self.assertTrue(all(result == results[0] for result in results))

    def test_before_and_after_write_failure_recovery(self):
        self.complete()
        before = self.files()
        with patch.object(run_admission, "_write", side_effect=OSError("before")):
            with self.assertRaises(OSError):
                self.bind()
        self.assertEqual(before, self.files())
        writer = run_admission._write

        def lost(record):
            writer(record)
            raise OSError("lost reply")

        with patch.object(run_admission, "_write", side_effect=lost):
            with self.assertRaises(OSError):
                self.bind()
        saved = self.files()
        self.bind()
        self.assertEqual(saved, self.files())

    def test_corrupt_or_noncanonical_policy_is_not_repaired(self):
        self.complete()
        self.bind()
        path = run_admission._path(self.run)
        original = path.read_bytes()
        for key, value in (
            ("version", 2),
            ("version", True),
            ("source_revision", 2),
            ("council_selection_sha256", "0" * 64),
            ("endpoint", "https://user:secret@provider.example"),
            ("max_tokens", True),
            ("max_tokens", 0),
            ("max_retries", -1),
            ("max_context_calls", "5"),
            ("retry_jitter", float("nan")),
            ("retry_max_seconds", 0.1),
            ("extra", "value"),
        ):
            path.write_bytes(original)
            self.rewrite(lambda r: r["council_request_policy"].__setitem__(key, value))
            before = self.files()
            with self.subTest(key=key):
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    run_inspection.inspect(self.run)
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    self.bind()
                self.assertEqual(before, self.files())
        for key in council_request_policy.OpenRouterRequestPolicy.model_fields:
            path.write_bytes(original)
            self.rewrite(lambda r: r["council_request_policy"].pop(key))
            with self.subTest(missing=key):
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    run_inspection.inspect(self.run)

    def test_missing_or_modified_source_and_initializing_policy_rejected(self):
        self.complete()
        self.bind()
        path = run_admission._path(self.run)
        original = path.read_bytes()
        for modify in (
            lambda r: r.pop("council_selection"),
            lambda r: r.pop("pre_query_policy"),
            lambda r: r["council_selection"].update(council_models=["different/model"]),
            lambda r: r.update(phase="initializing"),
        ):
            path.write_bytes(original)
            self.rewrite(modify)
            before = self.files()
            with self.assertRaises(run_admission.AdmissionUnavailable):
                run_inspection.inspect(self.run)
            self.assertEqual(before, self.files())

    def test_cancel_retains_read_evidence_and_blocks_rebind(self):
        self.complete()
        policy = self.bind().model_dump(mode="json")
        run_cancellation.request(self.run)
        before = self.files()
        snapshot = run_inspection.inspect(self.run)
        self.assertEqual(snapshot["internal_phase"], "cancelled")
        self.assertEqual(snapshot["council_request_policy"], policy)
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()
        self.assertEqual(before, self.files())

    def test_cancel_before_bind_and_deletion_prevent_attachment(self):
        self.complete()
        run_cancellation.request(self.run)
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()
        self.assertIsNone(run_inspection.inspect(self.run)["council_request_policy"])
        storage.delete_conversation(f"cli-{self.run}")
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.bind()
        self.assertNotIn(
            "council_request_policy",
            json.loads(run_admission._path(self.run).read_text()),
        )

    def test_deletion_erases_already_bound_policy(self):
        self.complete()
        self.bind()
        storage.delete_conversation(f"cli-{self.run}")
        with self.assertRaises(run_admission.AdmissionDeleted):
            run_inspection.inspect(self.run)
        self.assertNotIn(
            "council_request_policy",
            json.loads(run_admission._path(self.run).read_text()),
        )

    def test_fresh_process_read_uses_saved_not_current_configuration(self):
        self.complete()
        policy = self.bind().model_dump(mode="json")
        before = self.files()
        code = """import json,sys
from backend import config,storage,run_inspection
storage.DATA_DIR=sys.argv[1]
config.OPENROUTER_API_URL='https://different.example/chat'
config.OPENROUTER_MAX_TOKENS=1
config.MODEL_BACKEND='acp'
print(json.dumps(run_inspection.inspect(sys.argv[2])['council_request_policy']))
"""
        output = subprocess.check_output(
            [sys.executable, "-c", code, self.data, self.run], text=True, timeout=20
        )
        self.assertEqual(json.loads(output), policy)
        self.assertEqual(before, self.files())
