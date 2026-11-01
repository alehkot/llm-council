"""Offline wire fixtures and non-mutating legacy compatibility for CLI v1."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import interactive, storage
from backend.config import MAX_NEGATIVE_PROMPT_CHARS, MAX_PROMPT_CHARS
from backend.consultation_contract import (
    CLI_API_VERSION,
    TARGET_CLI_API_VERSION,
    AnswerRequest,
    CreateRunRequest,
    PromptSchema,
    ResultReference,
    RunLifecycle,
    RunReceipt,
    RunSnapshot,
)
from backend.export_markdown import build_markdown_export
from backend.interactive import get_pre_query_prompt
from backend.main import app
from backend.results import normalize_stage3

FIXTURES = Path(__file__).parent / "fixtures" / "cli"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class CliContractTests(unittest.TestCase):
    def test_create_fixture_preserves_canonical_text_and_options(self) -> None:
        payload = fixture("create")
        request = CreateRunRequest.model_validate(payload)
        self.assertEqual(request.model_dump(mode="json"), payload)
        self.assertEqual(payload, fixture("create"))

    def test_unicode_limits_count_code_points_not_utf8_bytes(self) -> None:
        payload = fixture("create")
        for field, limit in (
            ("content", MAX_PROMPT_CHARS),
            ("negative_prompt", MAX_NEGATIVE_PROMPT_CHARS),
        ):
            with self.subTest(field=field):
                accepted = CreateRunRequest.model_validate(
                    payload | {field: "🦫" * limit}
                )
                self.assertEqual(len(getattr(accepted, field)), limit)
                with self.assertRaises(ValidationError):
                    CreateRunRequest.model_validate(
                        payload | {field: "🦫" * (limit + 1)}
                    )

    def test_all_current_prompt_builders_fit_wire_schema(self) -> None:
        prompts = [
            interactive.get_pre_query_prompt(),
            interactive.get_prompt_refinement_prompt(
                {
                    "intent": "Clarify",
                    "refined_prompt": "Question",
                    "clarifying_questions": ["Which scope?"],
                },
                iteration=2,
            ),
            interactive.get_response_review_prompt(
                [{"model": "fake-a", "response": "answer"}], {"A": "fake-a"}
            ),
            interactive.get_synthesis_prefs_prompt(),
        ]
        for prompt in prompts:
            with self.subTest(prompt_id=prompt.prompt_id):
                parsed = PromptSchema.model_validate(prompt.to_dict())
                self.assertEqual(parsed.prompt_id, prompt.prompt_id)
                self.assertEqual(len(parsed.fields), len(prompt.fields))

    def test_create_rejects_unsafe_or_ambiguous_inputs(self) -> None:
        invalid = [
            {"content": " \n"},
            {"content": 12},
            {"refine": "false"},
            {"interaction_policy": "timeout"},
            {"recovery_policy": "automatic_retry"},
            {"model": "unapproved-override"},
            {"skill_ids": ["example", "example"]},
            {"skill_ids": ["../example"]},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                CreateRunRequest.model_validate(fixture("create") | changes)
        for field in ("interaction_policy", "recovery_policy"):
            payload = fixture("create")
            del payload[field]
            with self.assertRaises(ValidationError):
                CreateRunRequest.model_validate(payload)

    def test_prompt_fixture_and_repeated_logical_prompt_have_unique_instances(
        self,
    ) -> None:
        payload = fixture("waiting")
        first = RunSnapshot.model_validate(payload)
        payload["pending_prompt"]["instance_id"] = (
            "33333333-3333-4333-8333-333333333333"
        )
        second = RunSnapshot.model_validate(payload)
        self.assertEqual(
            first.pending_prompt.prompt.prompt_id,
            second.pending_prompt.prompt.prompt_id,
        )
        self.assertNotEqual(
            first.pending_prompt.instance_id, second.pending_prompt.instance_id
        )
        self.assertEqual(
            {f.type.value for f in first.pending_prompt.prompt.fields},
            {"select", "multi_select", "text", "checkbox"},
        )
        self.assertEqual(
            RunSnapshot.model_validate_json(first.model_dump_json()), first
        )

    def test_actual_web_prompt_shape_is_representable_without_changing_it(self) -> None:
        original = get_pre_query_prompt().to_dict()
        parsed = PromptSchema.model_validate(original)
        self.assertEqual(parsed.prompt_id, original["prompt_id"])
        for raw, field in zip(original["fields"], parsed.fields):
            self.assertEqual(raw["type"], field.type.value)
            self.assertEqual(raw.get("default"), field.default)
        self.assertEqual(original, get_pre_query_prompt().to_dict())

    def test_answer_fixture_preserves_bool_list_unicode_and_control_text(self) -> None:
        payload = fixture("answers")
        self.assertEqual(AnswerRequest.model_validate(payload).model_dump(), payload)
        for value in (1, 1.2, None, {}, [True], [3]):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                AnswerRequest(responses={"details": value})
        with self.assertRaises(ValidationError):
            AnswerRequest(responses={"details": "text"}, skipped=True)
        self.assertTrue(AnswerRequest(skipped=True).skipped)

    def test_lifecycle_is_distinct_from_stage_and_enforces_required_state(self) -> None:
        base = fixture("waiting")
        for changes in (
            {"lifecycle": "stage1"},
            {"pending_prompt": None},
            {"lifecycle": "running"},
            {
                "retry": {
                    "revision": "r1",
                    "operation": "answer",
                    "explanation": "uncertain",
                }
            },
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                RunSnapshot.model_validate(base | changes)
        wrong = copy.deepcopy(base)
        wrong["pending_prompt"]["run_id"] = "44444444-4444-4444-8444-444444444444"
        with self.assertRaises(ValidationError):
            RunSnapshot.model_validate(wrong)
        no_prompt = base | {"pending_prompt": None, "lifecycle": "needs_retry"}
        with self.assertRaises(ValidationError):
            RunSnapshot.model_validate(no_prompt)
        parsed = RunSnapshot.model_validate(
            no_prompt
            | {
                "retry": {
                    "revision": "revision-1",
                    "operation": "stage1:fake-a",
                    "explanation": "An unsaved provider response may be repeated.",
                }
            }
        )
        self.assertEqual(parsed.lifecycle, RunLifecycle.NEEDS_RETRY)

    def test_queued_receipt_and_terminal_reference_identity(self) -> None:
        receipt = {
            "run_id": fixture("waiting")["run_id"],
            "conversation_id": "example-conversation",
            "lifecycle": "queued",
        }
        self.assertIsNone(RunReceipt.model_validate(receipt).assistant_index)
        for index in (-1, True, "1"):
            with self.subTest(index=index), self.assertRaises(ValidationError):
                RunReceipt.model_validate(receipt | {"assistant_index": index})
        complete = receipt | {
            "lifecycle": "complete",
            "assistant_index": 1,
            "cursor": "c",
        }
        with self.assertRaises(ValidationError):
            RunSnapshot.model_validate(complete)
        reference = {
            "run_id": receipt["run_id"],
            "conversation_id": receipt["conversation_id"],
            "assistant_index": 1,
        }
        self.assertEqual(
            RunSnapshot.model_validate(
                complete | {"result": reference}
            ).result.assistant_index,
            1,
        )
        for key, value in (
            ("run_id", None),
            ("conversation_id", "other"),
            ("assistant_index", 2),
        ):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                RunSnapshot.model_validate(
                    complete | {"result": reference | {key: value}}
                )
        legacy = ResultReference(
            conversation_id="legacy-conversation", assistant_index=1
        )
        self.assertIsNone(legacy.run_id)

    def test_config_does_not_advertise_unimplemented_durable_routes(self) -> None:
        client = TestClient(app)
        response = client.get("/api/config", headers={"X-API-Key": "test-api-key"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cli_api_version"], CLI_API_VERSION)
        self.assertEqual(CLI_API_VERSION, 0)
        self.assertGreater(TARGET_CLI_API_VERSION, CLI_API_VERSION)
        self.assertIn("council_models", response.json())
        self.assertFalse(
            any(
                route.path in {"/api/consultations", "/api/consultations/{run_id}"}
                for route in app.routes
            )
        )


class LegacyResultCompatibilityTests(unittest.TestCase):
    def test_pure_normalizer_preserves_evidence_and_missing_dates(self) -> None:
        original = fixture("legacy-conversation")["messages"][1]["stage3"]
        snapshot = copy.deepcopy(original)
        view = normalize_stage3(original)
        self.assertEqual(view["current"], original)
        self.assertEqual(normalize_stage3(view), view)
        self.assertEqual(original, snapshot)
        view["current"]["usage"]["total_tokens"] = 999
        self.assertEqual(original["usage"]["total_tokens"], 12)
        self.assertEqual(view["versions"][0]["usage"]["total_tokens"], 12)
        undated = {"model": "fake", "response": "Unchanged"}
        self.assertIsNone(normalize_stage3(undated)["current"]["timestamp"])
        self.assertEqual(normalize_stage3(undated), normalize_stage3(undated))
        self.assertIsNone(normalize_stage3(None))

    def test_repeated_storage_reads_do_not_rewrite_legacy_file(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(storage, "DATA_DIR", directory),
        ):
            path = Path(directory) / "legacy-conversation.json"
            original_bytes = (FIXTURES / "legacy-conversation.json").read_bytes()
            path.write_bytes(original_bytes)
            before = path.stat().st_mtime_ns
            with patch.object(
                storage,
                "save_conversation",
                side_effect=AssertionError("Read attempted a write"),
            ):
                first = storage.get_conversation("legacy-conversation")
                second = storage.get_conversation("legacy-conversation")
            self.assertEqual(first, second)
            self.assertEqual(path.read_bytes(), original_bytes)
            self.assertEqual(path.stat().st_mtime_ns, before)
            self.assertNotIn("consultation_id", first["messages"][1]["metadata"])
            self.assertEqual(
                first["messages"][1]["stage3"]["current"]["timestamp"],
                "2020-01-02T03:05:06",
            )

    def test_legacy_and_current_exports_have_equivalent_saved_answers(self) -> None:
        conversation = fixture("legacy-conversation")
        legacy = conversation["messages"][1]
        current = copy.deepcopy(legacy)
        current["stage3"] = normalize_stage3(legacy["stage3"])
        left = build_markdown_export(conversation, legacy, "Original question", 1, 1)
        right = build_markdown_export(conversation, current, "Original question", 1, 1)

        # Export time is intentionally not a persisted result timestamp.
        def remove_time(text: str) -> str:
            return "\n".join(
                line
                for line in text.splitlines()
                if not line.startswith("- Exported At:")
            )

        self.assertEqual(remove_time(left), remove_time(right))
        self.assertIn("Original **Markdown** — русский 🦫", right)

    def test_undated_legacy_reads_are_stable_and_set_current_remains_explicit(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(storage, "DATA_DIR", directory),
        ):
            path = Path(directory) / "legacy-conversation.json"
            data = fixture("legacy-conversation")
            del data["messages"][1]["stage3"]["timestamp"]
            path.write_text(json.dumps(data))
            original = path.read_bytes()
            first = storage.get_conversation("legacy-conversation")
            second = storage.get_conversation("legacy-conversation")
            self.assertEqual(first, second)
            self.assertIsNone(first["messages"][1]["stage3"]["current"]["timestamp"])
            self.assertEqual(path.read_bytes(), original)
            storage.set_stage3_current_version("legacy-conversation", 1, 0)
            saved = json.loads(path.read_text())["messages"][1]["stage3"]
            self.assertEqual(saved["current"], saved["versions"][0])
            self.assertIsNone(saved["current"]["timestamp"])

    def test_explicit_update_still_saves_normalized_legacy_data(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(storage, "DATA_DIR", directory),
        ):
            path = Path(directory) / "legacy-conversation.json"
            path.write_bytes((FIXTURES / "legacy-conversation.json").read_bytes())
            version = {
                "model": "fake",
                "response": "New result",
                "mode": "consensus",
                "timestamp": "2020-02-01",
            }
            self.assertEqual(
                storage.append_stage3_version("legacy-conversation", 1, version), 1
            )
            saved = json.loads(path.read_text())
            self.assertEqual(saved["messages"][1]["stage3"]["current"], version)
            self.assertEqual(
                saved["messages"][1]["stage3"]["versions"][0]["usage"],
                {"total_tokens": 12},
            )
