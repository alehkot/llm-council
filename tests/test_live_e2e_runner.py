"""Offline checks of the opt-in runner; never execute its live providers."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.live_e2e import check_result, main


class LiveRunnerTests(unittest.TestCase):
    def test_requires_explicit_live_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "must-not-exist"
            with (
                patch("sys.argv", ["live_e2e.py", "--output-dir", str(output)]),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                main()
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(output.exists())

    def test_report_requires_full_flow_and_matching_disk_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "synthesis/run/result.md"
            result.parent.mkdir(parents=True)
            result.write_text("answer")
            message = {
                "role": "assistant",
                "stage1": [{"response": "draft", "failed": False}],
                "stage2": [{"validation_status": "valid"}],
                "negative": {"evaluations": [{"evaluation": "risk"}]},
                "metadata": {
                    "status": "complete",
                    "deliberation": {"results": [{"revised": "revision"}]},
                    "skills": {"resolved": [{"id": "investment-decision"}]},
                },
                "stage3": {
                    "current": {"model": "codex-cli/gpt-6-astra", "response": "answer"}
                },
            }
            conversation = {"messages": [message]}
            self.assertTrue(all(check_result(conversation, root, 1).values()))
            result.write_text("different answer")
            self.assertFalse(
                check_result(conversation, root, 1)["disk_result_matches_saved_answer"]
            )
            message["metadata"]["deliberation"]["results"] = []
            self.assertFalse(
                check_result(conversation, root, 1)["deliberation_present"]
            )
            self.assertFalse(all(check_result({"messages": []}, root, 1).values()))
