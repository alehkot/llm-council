"""Offline configured council selection with real durable receipt boundaries."""

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


class CouncilSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = str(self.root / "conversations")
        for target, name, value in (
            (storage, "DATA_DIR", self.data),
            (config, "MODEL_BACKEND", "openrouter"),
            (config, "COUNCIL_MODELS", ["provider/first", "provider/second"]),
            (config, "NEGATIVE_MODE_ENABLED", True),
            (config, "DELIBERATION_ENABLED", False),
            (config, "PAIRWISE_RANKING_ENABLED", True),
        ):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.run = self.prepare()

    def prepare(self):
        run = str(uuid4())
        run_admission.prepare(
            run,
            CreateRunRequest(
                content="Question — вопрос",
                negative_prompt="Explicit concern",
                interaction_policy="persistent",
                recovery_policy="explicit_retry",
            ),
        )
        return run

    def complete(self, run=None, bind_policy=True):
        run = run or self.run
        prompt = durable_prequery.advance(run, expected_revision=0)["current_prompt"]
        run_interaction.answer(run, prompt["instance_id"], AnswerRequest(responses={}))
        durable_prequery.advance(run, expected_revision=2)
        if bind_policy:
            prequery_policy.bind(run, expected_revision=3)

    def bind(self, run=None, revision=3):
        return council_selection.bind(run or self.run, expected_revision=revision)

    def files(self):
        return {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def rewrite(self, modify):
        p = run_admission._path(self.run)
        data = json.loads(p.read_text())
        modify(data)
        p.write_text(json.dumps(data))

    def test_exact_roster_order_flags_and_source_are_saved(self):
        self.complete()
        before = run_inspection.inspect(self.run)
        selected = self.bind()
        self.assertEqual(selected.council_models, ("provider/first", "provider/second"))
        self.assertEqual(
            (
                selected.negative_enabled,
                selected.deliberation_enabled,
                selected.pairwise_ranking_enabled,
            ),
            (True, False, True),
        )
        after = run_inspection.inspect(self.run)
        self.assertEqual(after["council_selection"], selected.model_dump(mode="json"))
        self.assertEqual(before["pre_query_policy"], after["pre_query_policy"])
        self.assertEqual(before["interaction"], after["interaction"])
        self.assertNotEqual(before["cursor"], after["cursor"])
        self.assertEqual(after["internal_phase"], "pre_query_complete")
        self.assertEqual(after["request"]["negative_prompt"], "Explicit concern")

    def test_stage_switch_combinations_and_request_do_not_invent_flags(self):
        from itertools import product

        for negative, deliberation, pairwise in product((False, True), repeat=3):
            run = self.prepare()
            self.complete(run=run)
            with (
                patch.object(config, "NEGATIVE_MODE_ENABLED", negative),
                patch.object(config, "DELIBERATION_ENABLED", deliberation),
                patch.object(config, "PAIRWISE_RANKING_ENABLED", pairwise),
            ):
                selected = self.bind(run)
            self.assertEqual(
                (
                    selected.negative_enabled,
                    selected.deliberation_enabled,
                    selected.pairwise_ranking_enabled,
                ),
                (negative, deliberation, pairwise),
            )
            self.assertEqual(
                run_inspection.inspect(run)["request"]["negative_prompt"],
                "Explicit concern",
            )

    def test_rebinding_and_inspection_are_byte_pure(self):
        self.complete()
        selected = self.bind()
        before = self.files()
        self.assertEqual(self.bind(), selected)
        run_inspection.inspect(self.run)
        self.assertEqual(before, self.files())

    def test_frozen_detached_and_no_secrets_or_synthesis_settings(self):
        self.complete()
        selected = self.bind()
        with self.assertRaises(ValueError):
            selected.negative_enabled = False
        config.COUNCIL_MODELS.append("provider/third")
        self.assertEqual(len(selected.council_models), 2)
        dumped = selected.model_dump(mode="json")
        dumped["council_models"].append("mutated")
        self.assertEqual(len(selected.council_models), 2)
        raw = json.dumps(run_inspection.inspect(self.run)["council_selection"])
        self.assertNotIn("test-openrouter-key", raw)
        self.assertNotIn("synthesis", raw)

    def test_each_config_drift_conflicts_but_historical_read_survives(self):
        self.complete()
        selected = self.bind().model_dump(mode="json")
        before = self.files()
        for key, value in (
            ("COUNCIL_MODELS", ["provider/second", "provider/first"]),
            ("COUNCIL_MODELS", ["provider/third"]),
            ("NEGATIVE_MODE_ENABLED", False),
            ("DELIBERATION_ENABLED", True),
            ("PAIRWISE_RANKING_ENABLED", False),
            ("MODEL_BACKEND", "acp"),
        ):
            with self.subTest(key=key), patch.object(config, key, value):
                self.assertEqual(
                    run_inspection.inspect(self.run)["council_selection"], selected
                )
                with self.assertRaises(council_selection.CouncilSelectionConflict):
                    self.bind()
                self.assertEqual(before, self.files())

    def test_unsupported_configuration_does_not_write(self):
        self.complete()
        before = self.files()
        for key, value in (
            ("MODEL_BACKEND", "acp"),
            ("MODEL_BACKEND", "unknown"),
            ("COUNCIL_MODELS", []),
            ("COUNCIL_MODELS", ["x", "x"]),
            ("COUNCIL_MODELS", [" x"]),
            ("COUNCIL_MODELS", ["x\n"]),
            ("COUNCIL_MODELS", [1]),
            ("COUNCIL_MODELS", "abc"),
            ("NEGATIVE_MODE_ENABLED", 1),
            ("DELIBERATION_ENABLED", "false"),
        ):
            with self.subTest(key=key, value=value), patch.object(config, key, value):
                with self.assertRaises(council_selection.CouncilSelectionConflict):
                    self.bind()
                self.assertEqual(before, self.files())

    def test_policy_and_completed_boundary_required(self):
        for revision in (0, 1):
            with self.assertRaises(
                (
                    council_selection.CouncilSelectionConflict,
                    run_interaction.PromptConflict,
                )
            ):
                self.bind(revision=revision)
        self.complete(bind_policy=False)
        with self.assertRaises(council_selection.CouncilSelectionConflict):
            self.bind()
        prequery_policy.bind(self.run, expected_revision=3)
        self.bind()

    def test_stale_and_malformed_revisions_cannot_return_existing_value(self):
        self.complete()
        self.bind()
        for revision in (0, 2, 4):
            with self.assertRaises(run_interaction.PromptConflict):
                self.bind(revision=revision)
        for revision in (True, "3", -1):
            with self.assertRaises(ValueError):
                self.bind(revision=revision)

    def test_parallel_duplicate_binds_have_one_write(self):
        self.complete()
        writer = run_admission._write
        with patch.object(run_admission, "_write", wraps=writer) as wrapped:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: self.bind(), range(24)))
        self.assertEqual(wrapped.call_count, 1)
        self.assertTrue(all(x == results[0] for x in results))

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

    def test_saved_corruption_is_rejected_without_repair(self):
        self.complete()
        self.bind()
        p = run_admission._path(self.run)
        original = p.read_bytes()
        for key, value in (
            ("version", 2),
            ("version", True),
            ("source_revision", 2),
            ("response_policy_sha256", "0" * 64),
            ("model_backend", "acp"),
            ("negative_enabled", "true"),
            ("council_models", ["x", "x"]),
            ("extra", "value"),
        ):
            p.write_bytes(original)
            self.rewrite(lambda r: r["council_selection"].__setitem__(key, value))
            before = self.files()
            with self.subTest(key=key):
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    run_inspection.inspect(self.run)
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    self.bind()
                self.assertEqual(before, self.files())
        p.write_bytes(original)
        self.rewrite(lambda r: r["council_selection"].pop("version"))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_inspection.inspect(self.run)

    def test_missing_source_and_initializing_selection_rejected(self):
        self.complete()
        self.bind()
        self.rewrite(lambda r: r.pop("pre_query_policy"))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_inspection.inspect(self.run)
        self.rewrite(lambda r: r.update(phase="initializing"))
        with self.assertRaises(run_admission.AdmissionUnavailable):
            run_inspection.inspect(self.run)

    def test_cancel_retains_read_evidence_and_blocks_bind(self):
        self.complete()
        selected = self.bind().model_dump(mode="json")
        run_cancellation.request(self.run)
        before = self.files()
        snapshot = run_inspection.inspect(self.run)
        self.assertEqual(snapshot["internal_phase"], "cancelled")
        self.assertEqual(snapshot["council_selection"], selected)
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()
        self.assertEqual(before, self.files())

    def test_cancel_before_bind_and_deletion_prevent_attachment(self):
        self.complete()
        run_cancellation.request(self.run)
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()
        self.assertIsNone(run_inspection.inspect(self.run)["council_selection"])
        storage.delete_conversation(f"cli-{self.run}")
        with self.assertRaises(run_admission.AdmissionDeleted):
            self.bind()
        self.assertNotIn(
            "council_selection", json.loads(run_admission._path(self.run).read_text())
        )

    def test_fresh_process_inspection_uses_saved_not_current_settings(self):
        self.complete()
        selected = self.bind().model_dump(mode="json")
        before = self.files()
        code = """import json,sys
from backend import config,storage,run_inspection
storage.DATA_DIR=sys.argv[1]
config.COUNCIL_MODELS=['changed/model']
config.MODEL_BACKEND='acp'
print(json.dumps(run_inspection.inspect(sys.argv[2])['council_selection']))
"""
        result = subprocess.check_output(
            [sys.executable, "-c", code, self.data, self.run], text=True, timeout=20
        )
        self.assertEqual(json.loads(result), selected)
        self.assertEqual(before, self.files())
