import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import agent_synthesis, council


class AgentSynthesisTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_export_and_result(self):
        artifacts = {
            "question": "Original question",
            "skills": "Selected reference",
            "guidance": {"strategy": "comprehensive"},
            "evidence": {"responses": [{"response": "FULL " * 20000}]},
        }
        for backend in ("codex", "claude"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                executable = Path(tmp) / "fake-cli"
                executable.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json,os,sys\nfrom pathlib import Path\n"
                    "assert 'OPENROUTER_API_KEY' not in os.environ\n"
                    "assert 'ANTHROPIC_API_KEY' not in os.environ\n"
                    "assert 'API_AUTH_KEY' not in os.environ\n"
                    "assert Path('AGENTS.md').is_file()\n"
                    "assert len(json.loads(Path('evidence/responses.json').read_text())[0]['response']) == 100000\n"
                    "if '--output-last-message' in sys.argv:\n"
                    " Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('Final answer')\n"
                    "else: print(json.dumps({'subtype':'success','is_error':False,'result':'Final answer'}))\n"
                )
                executable.chmod(0o700)
                with (
                    patch.object(agent_synthesis, "SYNTHESIS_AGENT_DIR", tmp),
                    patch.object(
                        agent_synthesis.shutil, "which", return_value=str(executable)
                    ),
                ):
                    result = await agent_synthesis.synthesize(backend, artifacts)
                self.assertEqual(result["response"], "Final answer")
                self.assertTrue(Path(result["workspace"], "result.md").exists())

    async def test_timeout_terminates_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "fake-cli"
            executable.write_text("#!/bin/sh\nsleep 30\n")
            executable.chmod(0o700)
            with (
                patch.object(agent_synthesis, "SYNTHESIS_AGENT_DIR", tmp),
                patch.object(agent_synthesis, "SYNTHESIS_AGENT_TIMEOUT_SECONDS", 0.05),
                patch.object(
                    agent_synthesis.shutil, "which", return_value=str(executable)
                ),
                self.assertRaises(RuntimeError),
            ):
                await agent_synthesis.synthesize(
                    "codex",
                    {
                        "question": "q",
                        "guidance": {},
                        "evidence": {},
                    },
                )

    async def test_stage3_bypasses_openrouter_and_preserves_evidence(self):
        expected = {
            "model": "codex-cli/default",
            "response": "answer",
            "workspace": "/tmp/example",
        }
        responses = [{"model": "model", "response": "full response"}]
        with (
            patch.object(council, "SYNTHESIS_BACKEND", "codex"),
            patch.object(
                agent_synthesis, "synthesize", new=AsyncMock(return_value=expected)
            ) as cli,
            patch.object(council, "query_model", new=AsyncMock()) as api,
        ):
            result = await council.stage3_synthesize_final(
                "question", responses, [], {}
            )
        self.assertEqual(result, expected)
        api.assert_not_called()
        self.assertEqual(cli.call_args.args[1]["evidence"]["responses"], responses)

    async def test_failure_does_not_import_partial_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "fake-cli"
            executable.write_text("#!/bin/sh\necho partial > result.md\nexit 1\n")
            executable.chmod(0o700)
            with (
                patch.object(agent_synthesis, "SYNTHESIS_AGENT_DIR", tmp),
                patch.object(
                    agent_synthesis.shutil, "which", return_value=str(executable)
                ),
                self.assertRaises(RuntimeError),
            ):
                await agent_synthesis.synthesize(
                    "codex",
                    {
                        "question": "q",
                        "guidance": {},
                        "evidence": {},
                    },
                )
