import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import consultation_journal, storage  # noqa: E402
from backend.consultation_manager import ConsultationManager  # noqa: E402
from backend.interactive import InteractiveConfig, PromptResponse  # noqa: E402
from backend.langgraph_flow import FlowContext, _stage3_prompt_modifier  # noqa: E402
from backend.trace import TraceCollector  # noqa: E402


class InteractiveWorkflowTests(unittest.TestCase):
    def test_invalid_interactive_synthesis_mode_is_normalized(self) -> None:
        config = InteractiveConfig.from_responses(
            {
                "synthesis_prefs": PromptResponse(
                    prompt_id="synthesis_prefs",
                    responses={"synthesis_mode": "not-a-mode", "focus_areas": "safety"},
                )
            }
        )

        self.assertEqual(config.synthesis_mode, "best_of_breed")
        self.assertEqual(config.focus_areas, "safety")

    def test_synthesis_focus_areas_apply_without_prompt_refinement(self) -> None:
        context = FlowContext(
            trace=TraceCollector(),
            prompt_refinement_enabled=False,
            collected_responses={
                "synthesis_prefs": PromptResponse(
                    prompt_id="synthesis_prefs",
                    responses={
                        "synthesis_mode": "weighted",
                        "focus_areas": "risk and migration sequencing",
                    },
                )
            },
        )
        context.refresh_interactive_config()

        modifier = _stage3_prompt_modifier(context)

        assert modifier is not None
        self.assertIn("Synthesis preference: weighted.", modifier)
        self.assertIn("Focus areas: risk and migration sequencing.", modifier)


class ConsultationCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_data_dir = storage.DATA_DIR
        self.old_consultation_dir = consultation_journal._CONSULTATION_DIR
        storage.DATA_DIR = str(Path(self.tempdir.name) / "conversations")
        consultation_journal._CONSULTATION_DIR = (
            Path(self.tempdir.name) / "consultations"
        )
        self.manager = ConsultationManager()
        self._reset_manager_state()

    def tearDown(self) -> None:
        self._reset_manager_state()
        storage.DATA_DIR = self.old_data_dir
        consultation_journal._CONSULTATION_DIR = self.old_consultation_dir
        self.tempdir.cleanup()

    def _reset_manager_state(self) -> None:
        with self.manager._lock:
            self.manager._consultations.clear()
            self.manager._queues.clear()
            self.manager._futures.clear()
            self.manager._interactive_sessions.clear()

    def test_cancelled_run_cannot_be_persisted_as_complete(self) -> None:
        conversation_id = "canceltest"
        consultation_id = "jobcanceltest"
        storage.create_conversation(conversation_id)
        state = self.manager._create_started_state(
            consultation_id,
            conversation_id,
            "Should be cancelled",
            None,
            resume_from_storage=False,
        )
        with self.manager._lock:
            self.manager._consultations[conversation_id] = state
        state.cancel_event.set()

        async def fake_run_council_graph(*_args, **_kwargs):
            return {
                "stage1": [{"model": "model-a", "response": "answer"}],
                "stage2": [],
                "stage3": {
                    "current": {"model": "chair", "response": "final"},
                    "versions": [],
                },
                "metadata": {"some": "metadata"},
            }

        with patch("backend.langgraph_flow.run_council_graph", fake_run_council_graph):
            self.manager._run_consultation(state)

        conversation = storage.get_conversation(conversation_id)
        assert conversation is not None
        assistant_messages = [
            message
            for message in conversation["messages"]
            if message.get("role") == "assistant"
        ]
        self.assertEqual(len(assistant_messages), 1)
        metadata = assistant_messages[0].get("metadata") or {}
        self.assertEqual(metadata.get("status"), "failed")
        self.assertEqual(metadata.get("error"), "Cancelled")
        self.assertNotEqual(metadata.get("status"), "complete")


if __name__ == "__main__":
    unittest.main()
