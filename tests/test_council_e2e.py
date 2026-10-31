"""Offline API-to-disk council tests. Only provider/CLI boundaries are mocked."""

import json
import os
import socket
import tempfile
import time
import unittest
from collections import Counter
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from fastapi.testclient import TestClient

from backend import (
    agent_synthesis,
    consultation_journal,
    consultation_manager,
    council,
    langgraph_flow,
    main,
    openrouter,
    skills,
    storage,
    typesafe,
)

QUESTION = "Should our small bakery lease a second oven? Compare a trial rental with buying; demand is uncertain."
FINAL = "Run a short oven rental trial; measure peak demand and margin before committing to a purchase."
MODELS = ["test/operations", "test/finance"]


class CouncilE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls = []
        self.jev_requests = []
        self.cli_calls = []
        self.unexpected_network = []
        self.cli_failure = False
        self.failed_member = False

        def replace(obj, name, value):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

        # An isolated real manager/executor avoids touching singleton jobs.
        manager = object.__new__(consultation_manager.ConsultationManager)
        manager._initialized = False
        manager.__init__()
        self.manager = manager
        replace(main, "consultation_manager", manager)
        replace(storage, "DATA_DIR", str(self.root / "conversations"))
        replace(consultation_journal, "_CONSULTATION_DIR", self.root / "jobs")
        replace(skills, "_SKILLS_DIR", self.root / "skills")
        replace(agent_synthesis, "SYNTHESIS_AGENT_DIR", str(self.root / "synthesis"))
        replace(agent_synthesis, "SYNTHESIS_AGENT_MODEL", "gpt-6-astra")
        replace(agent_synthesis, "SYNTHESIS_AGENT_EFFORT", "xhigh")
        for module in (council, langgraph_flow):
            replace(module, "COUNCIL_MODELS", MODELS)
        for name, value in {
            "NEGATIVE_MODE_ENABLED": True,
            "DELIBERATION_ENABLED": True,
            "INTERACTIVE_MODE_ENABLED": False,
            "PROMPT_REFINEMENT_ENABLED": False,
        }.items():
            replace(consultation_manager, name, value)
        for name in (
            "SKILLS_ENABLED",
            "SKILLS_AUTO_SELECT_ENABLED",
            "SKILLS_REFERENCE_SELECTION_ENABLED",
        ):
            replace(langgraph_flow, name, True)
        replace(council, "SKILLS_TYPESAFE_ENABLED", True)
        replace(typesafe, "TYPESAFE_API_KEY", "mock-jev-key")
        replace(council, "SYNTHESIS_BACKEND", "codex")
        replace(council, "SYNTHESIS_MODEL", "codex-cli/gpt-6-astra")
        replace(langgraph_flow, "SYNTHESIS_MODEL", "codex-cli/gpt-6-astra")
        replace(openrouter, "_query_model_once", self.model_response)
        replace(
            openrouter,
            "model_limits",
            AsyncMock(
                return_value={
                    "context_length": 1000000,
                    "max_completion_tokens": 128000,
                    "supported_parameters": ["reasoning"],
                }
            ),
        )
        real_client = httpx.AsyncClient
        replace(
            httpx,
            "AsyncClient",
            lambda **kw: real_client(
                transport=httpx.MockTransport(self.jev_response), **kw
            ),
        )
        replace(agent_synthesis.shutil, "which", lambda name: f"/mock/{name}")
        replace(agent_synthesis.asyncio, "create_subprocess_exec", self.cli_response)
        real_connect = socket.socket.connect

        def no_network(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                self.unexpected_network.append(str(address))
                raise AssertionError(f"External network forbidden: {address}")
            return real_connect(sock, address)

        replace(socket.socket, "connect", no_network)
        self.client = TestClient(main.app)
        self.client.headers["X-API-Key"] = main.API_AUTH_KEY
        self.addCleanup(self.client.close)
        # Drain workers before restoring mocks or deleting artifacts, even on failure.
        self.addCleanup(lambda: manager._executor.shutdown(wait=True))

    def jev_response(self, request: httpx.Request) -> httpx.Response:
        self.assertEqual(str(request.url), "https://api.typesafe.ai/v1/systemone")
        payload = json.loads(request.content)
        self.jev_requests.append(payload)
        answers = {
            str(key): {
                "type": "score",
                "score": 0 if "gardening" in json.dumps(candidate).lower() else 2,
            }
            for key, candidate in payload["state"]["candidates"].items()
        }
        return httpx.Response(200, json={"model": "jev-mock", "answers": answers})

    async def model_response(
        self, model, messages, timeout, trace, stage, context, **kwargs
    ):
        self.calls.append((stage, model, messages))
        if stage == "stage1" and self.failed_member and model == MODELS[1]:
            return None
        if stage == "title":
            content = "Bakery oven decision"
        elif stage == "stage1":
            self.assertIn("RENTAL-REFERENCE", str(messages))
            self.assertNotIn("GARDENING-REFERENCE", str(messages))
            content = (
                f"{model}: trial rental limits irreversible investment; measure demand."
            )
        elif stage == "negative_eval":
            content = json.dumps(
                {
                    "self_score": 7,
                    "self_summary": "Check rental cost.",
                    "peer_scores": {label: 8 for label in context["peer_labels"]},
                    "peer_summaries": {
                        label: "Check demand uncertainty."
                        for label in context["peer_labels"]
                    },
                }
            )
        elif stage == "stage2":
            count = 1 if self.failed_member else 2
            content = (
                "Favor reversible capacity decisions.\nFINAL RANKING:\n"
                + "\n".join(f"{i + 1}. Response {chr(65 + i)}" for i in range(count))
            )
        elif stage == "deliberation":
            content = f"REVISED {model}: compare total rental cost and track peak demand before buying."
        else:
            raise AssertionError(f"Unexpected model call: {stage}")
        return {
            "content": content,
            "finish_reason": "stop",
            "usage": {"total_tokens": 50},
        }

    async def cli_response(self, *command, **kwargs):
        workspace = Path(kwargs["cwd"])
        self.cli_calls.append((command, workspace))
        self.assertEqual((workspace / "question.md").read_text(), QUESTION)
        self.assertIn("RENTAL-REFERENCE", (workspace / "skills.md").read_text())
        self.assertNotIn("GARDENING-REFERENCE", (workspace / "skills.md").read_text())
        self.assertTrue((workspace / "AGENTS.md").is_file())
        evidence = workspace / "evidence"
        responses = json.loads((evidence / "responses.json").read_text())
        self.assertTrue(all(r["response"].startswith("REVISED") for r in responses))
        for filename in (
            "peer_reviews",
            "rankings",
            "negative_evaluations",
            "label_to_model",
        ):
            self.assertTrue(json.loads((evidence / f"{filename}.json").read_text()))
        self.assertIsInstance(
            json.loads((evidence / "gap_scores.json").read_text()), list
        )
        self.assertTrue(
            json.loads((workspace / "guidance.json").read_text())[
                "using_deliberation_revisions"
            ]
        )
        self.assertNotIn("OPENROUTER_API_KEY", kwargs["env"])
        self.assertNotIn("API_AUTH_KEY", kwargs["env"])
        if command[0] == "/mock/codex":
            self.assertIn('model_reasoning_effort="xhigh"', command)
            self.assertEqual(command[command.index("--model") + 1], "gpt-6-astra")
            Path(command[command.index("--output-last-message") + 1]).write_text(FINAL)
        else:
            kwargs["stdout"].write(
                json.dumps(
                    {"subtype": "success", "is_error": False, "result": FINAL}
                ).encode()
            )
        return SimpleNamespace(
            returncode=1 if self.cli_failure else 0, wait=AsyncMock(return_value=0)
        )

    def start_and_wait(self):
        archive = BytesIO()
        with ZipFile(archive, "w") as pack:
            pack.writestr(
                "oven-decision/SKILL.md",
                "---\nname: oven-decision\ndescription: Decide on bakery ovens under uncertain demand.\n---\nCompare reversible options. Read [rental](references/rental.md).\n",
            )
            pack.writestr(
                "oven-decision/references/rental.md",
                "# Rental trial\nRENTAL-REFERENCE: measure demand before irreversible investment.\n",
            )
            pack.writestr(
                "gardening/SKILL.md",
                "---\nname: gardening\ndescription: Gardening and soil care.\n---\nGARDENING-REFERENCE: plant bulbs.\n",
            )
        uploaded = self.client.post(
            "/api/skills?filename=sample.zip",
            content=archive.getvalue(),
            headers={"Content-Type": "application/zip"},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        created = self.client.post("/api/conversations", json={})
        self.assertEqual(created.status_code, 200, created.text)
        conversation_id = created.json()["id"]
        self.conversation_id = conversation_id
        submitted = self.client.post(
            f"/api/conversations/{conversation_id}/message",
            json={
                "content": QUESTION,
                "negative_prompt": "Look for rental costs and uncertain demand.",
            },
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = self.client.get(
                f"/api/conversations/{conversation_id}/consultation/status"
            )
            self.assertEqual(response.status_code, 200)
            snapshot = response.json()
            if snapshot["status"] in {"complete", "failed"}:
                break
            time.sleep(0.01)
        else:
            self.fail("Mocked consultation did not finish in 10 seconds")
        self.manager._executor.shutdown(wait=True)
        saved = self.client.get(f"/api/conversations/{conversation_id}").json()
        self.assertEqual(self.unexpected_network, [])
        self.assertGreaterEqual(len(self.jev_requests), 2)
        self.assertNotIn("stage3", [call[0] for call in self.calls])
        self.assertEqual(len(self.cli_calls), 1)
        return snapshot, saved["messages"][-1]

    def test_codex_full_flow_persists_synthesis(self) -> None:
        snapshot, message = self.start_and_wait()
        self.assertEqual(snapshot["status"], "complete", snapshot)
        self.assertEqual(message["metadata"]["status"], "complete")
        self.assertEqual(message["stage3"]["current"]["response"], FINAL)
        self.assertEqual(message["stage3"]["current"]["model"], "codex-cli/gpt-6-astra")
        self.assertEqual(len(message["stage3"]["versions"]), 1)
        self.assertEqual(len(message["stage1"]), 2)
        self.assertEqual(len(message["stage2"]), 2)
        counts = Counter(call[0] for call in self.calls)
        self.assertEqual(
            counts,
            {
                "title": 1,
                "stage1": 2,
                "negative_eval": 2,
                "stage2": 2,
                "deliberation": 2,
            },
        )
        workspace = self.cli_calls[0][1]
        self.assertEqual((workspace / "result.md").read_text(), FINAL)

    def test_claude_full_flow_with_one_failed_council_member(self) -> None:
        self.failed_member = True
        with (
            patch.object(council, "SYNTHESIS_BACKEND", "claude"),
            patch.object(agent_synthesis, "SYNTHESIS_AGENT_MODEL", "opus"),
        ):
            snapshot, message = self.start_and_wait()
        self.assertEqual(snapshot["status"], "complete", snapshot)
        self.assertEqual(sum(not r["failed"] for r in message["stage1"]), 1)
        self.assertEqual(message["stage3"]["current"]["response"], FINAL)
        self.assertEqual(message["stage3"]["current"]["model"], "claude-cli/opus")

    def test_cli_failure_persists_failure_without_partial_synthesis(self) -> None:
        self.cli_failure = True
        with patch("traceback.print_exc") as logged:
            snapshot, message = self.start_and_wait()
        logged.assert_called_once()
        self.assertEqual(snapshot["status"], "failed", snapshot)
        self.assertEqual(message["metadata"]["status"], "failed")
        self.assertIn("synthesis failed", message["metadata"]["error"])
        self.assertFalse(message.get("stage3"))
        self.assertEqual(len(message["stage1"]), 2)
        self.assertTrue(self.cli_calls[0][1].is_dir())

    def test_resynthesis_replays_evidence_without_repeating_model_calls(self) -> None:
        snapshot, _ = self.start_and_wait()
        self.assertEqual(snapshot["status"], "complete")
        previous_calls = len(self.calls)
        previous_jev = len(self.jev_requests)
        response = self.client.post(
            f"/api/conversations/{self.conversation_id}/resynthesize",
            json={
                "message_index": 0,
                "synthesis_mode": "comprehensive",
                "focus_areas": "cash flow",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.calls), previous_calls)
        self.assertEqual(len(self.jev_requests), previous_jev)
        self.assertEqual(len(self.cli_calls), 2)
        self.assertNotEqual(self.cli_calls[0][1], self.cli_calls[1][1])
        guidance = json.loads((self.cli_calls[1][1] / "guidance.json").read_text())
        self.assertEqual(guidance["strategy"], "comprehensive")
        self.assertIn("cash flow", guidance["additional_guidance"])
        saved = self.client.get(f"/api/conversations/{self.conversation_id}").json()
        stage3 = saved["messages"][-1]["stage3"]
        self.assertEqual(len(stage3["versions"]), 2)
        self.assertEqual(stage3["current"]["response"], FINAL)
        self.assertEqual(stage3["current"]["mode"], "comprehensive")
