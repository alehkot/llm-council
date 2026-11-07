"""Offline deterministic skill preparation and durable exact rendered inputs."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_run_skill_rendering as fixtures

from backend import (
    config,
    durable_prequery,
    langgraph_flow,
    prequery_policy,
    run_admission,
    run_cancellation,
    run_inspection,
    run_interaction,
    run_skill_plan,
    run_skill_rendering,
    skills,
    storage,
)
from backend.consultation_contract import AnswerRequest

SETTINGS = {
    "SKILLS_ENABLED": True,
    "SKILLS_REFERENCE_SELECTION_ENABLED": False,
    "SKILLS_MAX_REFERENCES": 2,
    "SKILLS_MAX_REFERENCE_SECTIONS": 3,
    "SKILLS_CONTEXT_MAX_CHARS": 4000,
    "SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS": 1600,
}


class DurableSkillPlanTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ArchivedSkillRenderingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.run, self.root = self.fixture.run, self.fixture.root
        for key, value in SETTINGS.items():
            p = patch.object(config, key, value)
            p.start()
            self.addCleanup(p.stop)

    def complete(self):
        prompt = durable_prequery.advance(self.run, expected_revision=0)[
            "current_prompt"
        ]
        run_interaction.answer(
            self.run, prompt["instance_id"], AnswerRequest(responses={})
        )
        durable_prequery.advance(self.run, expected_revision=2)
        self.policy = prequery_policy.bind(self.run, expected_revision=3)

    def bind(self, revision=3):
        return run_skill_plan.bind(self.run, expected_revision=revision)

    def files(self):
        return self.fixture.files()

    def rewrite(self, mutate):
        path = run_admission._path(self.run)
        record = json.loads(path.read_text())
        mutate(record)
        path.write_text(json.dumps(record))

    def test_exact_choices_blocks_reports_and_compact_inspection(self):
        self.complete()
        before = run_inspection.inspect(self.run)
        plan = self.bind()
        self.assertEqual(plan.source_revision, 3)
        self.assertEqual(plan.settings.selection_mode, "router_disabled")
        self.assertEqual(plan.candidate_count, 4)
        self.assertTrue(plan.selected_candidates)
        self.assertEqual(
            plan.response_policy_sha256,
            run_skill_plan._digest(self.policy.model_dump(mode="json")),
        )
        self.assertEqual(
            plan.archive_sha256, run_skill_plan._digest(self.fixture.identity)
        )
        selection = {}
        for ref in plan.selected_references:
            selection.setdefault(ref.skill_id, []).append(
                skills.SectionSelection(ref.path, ref.section_ids)
            )
        for limit, block in ((4000, plan.stage1), (1600, plan.stage3)):
            text, report = self.fixture.render(selection, limit)
            self.assertEqual(block.text, text)
            self.assertEqual(block.report(), report)
            self.assertLessEqual(len(text or ""), limit)
        after = run_inspection.inspect(self.run)
        self.assertNotEqual(before["cursor"], after["cursor"])
        self.assertEqual(after["skill_plan"], run_skill_plan.summary(plan))
        self.assertNotIn("Original instructions", json.dumps(after))
        self.assertNotIn("Alpha body", json.dumps(after))
        for key in ("interaction", "request", "pre_query_policy", "internal_phase"):
            self.assertEqual(before[key], after[key])

    def test_real_legacy_router_disabled_path_has_matching_choices_and_blocks(self):
        self.complete()
        plan = self.bind()
        loaded = [skills.get_skill("one")]
        candidates = skills.build_reference_candidates(loaded)
        meta = {}
        with (
            patch.object(langgraph_flow, "SKILLS_REFERENCE_SELECTION_ENABLED", False),
            patch.object(langgraph_flow, "SKILLS_CONTEXT_MAX_CHARS", 4000),
            patch.object(langgraph_flow, "SKILLS_MAX_REFERENCES", 2),
            patch.object(langgraph_flow, "SKILLS_MAX_REFERENCE_SECTIONS", 3),
            patch.object(
                langgraph_flow,
                "select_skill_references",
                side_effect=AssertionError("provider"),
            ),
        ):
            ids, reasons = asyncio.run(
                langgraph_flow._resolve_reference_selection(
                    None,
                    {"user_query": self.policy.user_query},
                    candidates,
                    meta,
                    loaded,
                )
            )
        self.assertEqual(tuple(ids), plan.selected_candidates)
        self.assertEqual(reasons, {})
        self.assertEqual(meta["fallback"], "router_disabled")

    def test_enabled_router_and_disabled_skills_are_never_silently_bypassed(self):
        self.complete()
        before = self.files()
        for key, value in (
            ("SKILLS_REFERENCE_SELECTION_ENABLED", True),
            ("SKILLS_REFERENCE_SELECTION_ENABLED", 0),
            ("SKILLS_ENABLED", False),
            ("SKILLS_ENABLED", 1),
        ):
            with patch.object(config, key, value):
                with self.assertRaises(run_skill_plan.SkillPlanConflict):
                    self.bind()
            self.assertEqual(before, self.files())

    def test_missing_prerequisites_and_stale_revisions_write_nothing(self):
        before = self.files()
        with self.assertRaises(run_skill_plan.SkillPlanConflict):
            self.bind(0)
        self.assertEqual(before, self.files())
        self.complete()
        for revision in (0, 2, 4, -1, True, "3", 3.0):
            before = self.files()
            with self.assertRaises(ValueError):
                self.bind(revision)
            self.assertEqual(before, self.files())
        self.rewrite(lambda r: r.pop("explicit_skill_snapshot"))
        before = self.files()
        with self.assertRaises(run_skill_plan.SkillPlanConflict):
            self.bind()
        self.assertEqual(before, self.files())

    def test_initial_and_repeat_binds_use_archived_bytes_after_library_deletion(self):
        self.complete()
        skills.delete_skill("one")
        with (
            patch.object(skills, "get_skill", side_effect=AssertionError("library")),
            patch.object(
                skills, "read_skill_markdown", side_effect=AssertionError("body")
            ),
            patch.object(
                skills,
                "read_reference_sections",
                side_effect=AssertionError("references"),
            ),
        ):
            plan = self.bind()
            before = self.files()
            self.assertEqual(self.bind(), plan)
            self.assertEqual(run_skill_plan.load(self.run), plan)
            run_inspection.inspect(self.run)
            self.assertEqual(before, self.files())

    def test_saved_blocks_and_reports_are_frozen_detached_and_repr_safe(self):
        self.complete()
        plan = self.bind()
        with self.assertRaises(ValueError):
            plan.stage1.text = "changed"
        with self.assertRaises(ValueError):
            plan.settings.stage1_max_chars = 1
        copy_report = plan.stage1.report()
        copy_report["resolved"].clear()
        copy_value = plan.model_dump(mode="json")
        copy_value["selected_references"].clear()
        self.assertEqual(run_skill_plan.load(self.run), plan)
        self.assertNotIn("Original instructions", repr(plan))
        self.assertNotIn("Alpha body", repr(plan))

    def test_config_drift_conflicts_on_repeat_but_history_uses_saved_settings(self):
        self.complete()
        plan = self.bind()
        before = self.files()
        for key, value in SETTINGS.items():
            different = not value if type(value) is bool else value + 1
            with patch.object(config, key, different):
                self.assertEqual(run_skill_plan.load(self.run), plan)
                run_inspection.inspect(self.run)
                with self.assertRaises(run_skill_plan.SkillPlanConflict):
                    self.bind()
                self.assertEqual(before, self.files())
        with patch.object(
            run_skill_plan, "_current", side_effect=AssertionError("live config")
        ):
            self.assertEqual(run_skill_plan.load(self.run), plan)

    def test_irrelevant_auto_selection_and_credentials_do_not_change_explicit_plan(
        self,
    ):
        self.complete()
        plan = self.bind()
        with (
            patch.object(
                config,
                "SKILLS_AUTO_SELECT_ENABLED",
                not config.SKILLS_AUTO_SELECT_ENABLED,
            ),
            patch.object(config, "OPENROUTER_API_KEY", "synthetic-secret-do-not-store"),
            patch.object(config, "SKILLS_REFERENCE_SELECTION_MODEL", "different/model"),
        ):
            self.assertEqual(self.bind(), plan)
        self.assertNotIn(
            "synthetic-secret-do-not-store", run_admission._path(self.run).read_text()
        )

    def test_strict_settings_and_unrenderable_framing_fail_without_write(self):
        self.complete()
        for key in (
            "SKILLS_MAX_REFERENCES",
            "SKILLS_MAX_REFERENCE_SECTIONS",
            "SKILLS_CONTEXT_MAX_CHARS",
            "SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS",
        ):
            for value in (-1, True, 1.5, "100", None):
                before = self.files()
                with patch.object(config, key, value):
                    with self.assertRaises(run_skill_plan.SkillPlanConflict):
                        self.bind()
                self.assertEqual(before, self.files())
        for key in ("SKILLS_CONTEXT_MAX_CHARS", "SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS"):
            before = self.files()
            with patch.object(config, key, 1):
                with self.assertRaises(run_skill_plan.SkillPlanConflict):
                    self.bind()
            self.assertEqual(before, self.files())

    def test_zero_budgets_are_real_saved_decisions(self):
        self.complete()
        with (
            patch.object(config, "SKILLS_CONTEXT_MAX_CHARS", 0),
            patch.object(config, "SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS", 0),
        ):
            plan = self.bind()
        self.assertEqual(plan.reference_budget, 0)
        self.assertEqual(plan.selected_candidates, ())
        self.assertIsNone(plan.stage1.text)
        self.assertIsNone(plan.stage3.text)
        self.assertEqual(run_skill_plan.load(self.run), plan)

    def test_zero_file_or_section_caps_save_empty_reference_choices(self):
        for key in ("SKILLS_MAX_REFERENCES", "SKILLS_MAX_REFERENCE_SECTIONS"):
            test = DurableSkillPlanTests()
            test.setUp()
            try:
                test.complete()
                with patch.object(config, key, 0):
                    plan = test.bind()
                self.assertEqual(plan.selected_candidates, ())
                self.assertTrue(plan.stage1.text)
                self.assertEqual(plan.stage1.report()["resolved"][0]["references"], [])
                self.assertEqual(run_skill_plan.load(test.run), plan)
            finally:
                test.doCleanups()

    def test_cancel_and_delete_serialize_after_preparation_without_resurrection(self):
        for action in ("cancel", "delete"):
            test = DurableSkillPlanTests()
            test.setUp()
            try:
                test.complete()
                entered, release, mutating = (threading.Event() for _ in range(3))
                original = run_skill_rendering._render_captured

                def blocked(*args, **kwargs):
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("release timed out")
                    return original(*args, **kwargs)

                def mutate():
                    mutating.set()
                    return (
                        run_cancellation.request(test.run)
                        if action == "cancel"
                        else storage.delete_conversation("cli-" + test.run)
                    )

                with (
                    patch.object(
                        run_skill_rendering, "_render_captured", side_effect=blocked
                    ),
                    ThreadPoolExecutor(max_workers=2) as pool,
                ):
                    preparation = pool.submit(test.bind)
                    self.assertTrue(entered.wait(3))
                    mutation = pool.submit(mutate)
                    self.assertTrue(mutating.wait(3))
                    self.assertFalse(mutation.done())
                    release.set()
                    plan = preparation.result(timeout=3)
                    mutation.result(timeout=3)
                if action == "cancel":
                    self.assertEqual(run_skill_plan.load(test.run), plan)
                    with self.assertRaises(run_admission.AdmissionCancelled):
                        test.bind()
                else:
                    self.assertNotIn("skill_plan", run_admission.get(test.run))
                    with self.assertRaises(run_admission.AdmissionDeleted):
                        test.bind()
            finally:
                test.doCleanups()

    def test_concurrent_duplicates_write_one_exact_plan(self):
        self.complete()
        with (
            patch.object(run_admission, "_write", wraps=run_admission._write) as writes,
            ThreadPoolExecutor(max_workers=8) as pool,
        ):
            plans = list(pool.map(lambda _: self.bind(), range(24)))
        self.assertTrue(all(plan == plans[0] for plan in plans))
        self.assertEqual(writes.call_count, 1)

    def test_failed_and_lost_write_acknowledgements_preserve_recovery(self):
        self.complete()
        before = self.files()
        with patch.object(run_admission, "_write", side_effect=OSError("failed write")):
            with self.assertRaises(OSError):
                self.bind()
        self.assertEqual(before, self.files())
        write = run_admission._write

        def lost(record):
            write(record)
            raise OSError("lost acknowledgement")

        with patch.object(run_admission, "_write", side_effect=lost):
            with self.assertRaises(OSError):
                self.bind()
        plan = run_skill_plan.load(self.run)
        before = self.files()
        self.assertEqual(self.bind(), plan)
        self.assertEqual(before, self.files())

    def test_cancellation_preserves_history_and_blocks_any_mutation(self):
        self.complete()
        plan = self.bind()
        run_cancellation.request(self.run)
        before = self.files()
        self.assertEqual(run_skill_plan.load(self.run), plan)
        self.assertEqual(
            run_inspection.inspect(self.run)["internal_phase"], "cancelled"
        )
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()
        self.assertEqual(before, self.files())

    def test_cancellation_before_first_bind_cannot_create_a_plan(self):
        self.complete()
        run_cancellation.request(self.run)
        before = self.files()
        with self.assertRaises(run_admission.AdmissionCancelled):
            self.bind()
        self.assertNotIn("skill_plan", run_admission.get(self.run))
        self.assertEqual(before, self.files())

    def test_deletion_removes_blocks_and_prevents_resurrection(self):
        self.complete()
        self.bind()
        storage.delete_conversation("cli-" + self.run)
        before = self.files()
        for action in (self.bind, lambda: run_skill_plan.load(self.run)):
            with self.assertRaises(run_admission.AdmissionDeleted):
                action()
        raw = run_admission._path(self.run).read_text()
        self.assertNotIn("skill_plan", raw)
        self.assertNotIn("Original instructions", raw)
        self.assertEqual(before, self.files())

    def test_saved_corruption_fails_closed_without_repair(self):
        self.complete()
        self.bind()
        path = run_admission._path(self.run)
        original = json.loads(path.read_text())
        mutations = (
            lambda r: r.update(phase="initializing"),
            lambda r: r.pop("pre_query_policy"),
            lambda r: r.pop("explicit_skill_snapshot"),
            lambda r: r["skill_plan"].update(version=True),
            lambda r: r["skill_plan"].update(source_revision=4),
            lambda r: r["skill_plan"].update(response_policy_sha256="0" * 64),
            lambda r: r["skill_plan"].update(archive_sha256="0" * 64),
            lambda r: r["skill_plan"].update(selected_candidates=[999]),
            lambda r: r["skill_plan"].update(selected_references=[]),
            lambda r: r["skill_plan"]["settings"].update(
                selection_mode="router_failed"
            ),
            lambda r: r["skill_plan"]["stage1"].update(text="changed"),
            lambda r: r["skill_plan"]["stage3"].update(report_json="{}"),
            lambda r: r["skill_plan"].update(candidate_count=999),
            lambda r: r["skill_plan"].update(reference_budget=999),
        )
        for mutate in mutations:
            data = copy.deepcopy(original)
            mutate(data)
            path.write_text(json.dumps(data))
            before = self.files()
            for action in (
                self.bind,
                lambda: run_skill_plan.load(self.run),
                lambda: run_inspection.inspect(self.run),
            ):
                with self.assertRaises(run_admission.AdmissionUnavailable):
                    action()
            self.assertEqual(before, self.files())

    def test_fresh_process_load_has_no_current_config_or_library_dependency(self):
        self.complete()
        plan = self.bind()
        skills.delete_skill("one")
        before = self.files()
        code = """
import json, sys
from unittest.mock import patch
from backend import run_skill_plan, skills, storage
storage.DATA_DIR = sys.argv[1]
with patch.object(run_skill_plan, '_current', side_effect=AssertionError('config')), patch.object(skills, 'get_skill', side_effect=AssertionError('library')):
    result = run_skill_plan.load(sys.argv[2])
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


if __name__ == "__main__":
    unittest.main()
