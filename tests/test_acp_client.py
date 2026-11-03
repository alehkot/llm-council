import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import acp_client, agent_synthesis, council, model_client
from backend.skills import ReferenceCandidate, Skill
from backend.trace import TraceCollector

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "fake_acp_agent.py"


class ACPClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.log = Path(self.directory.name) / "events.jsonl"

    def launch(self, scenario: str = "success") -> Any:
        env = {key: os.environ[key] for key in ("HOME", "PATH") if key in os.environ}
        return patch.object(
            acp_client,
            "launch_settings",
            return_value=(
                [sys.executable, str(FIXTURE), scenario, str(self.log)],
                env,
                {},
            ),
        )

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    async def test_protocol_preserves_context_and_selects_exact_model(self) -> None:
        messages = [
            {"role": "system", "content": "System ☃"},
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Previous answer"},
            {"role": "user", "content": "Last"},
        ]
        trace = TraceCollector()
        with self.launch():
            result = await acp_client.query_model(
                "codex/native-other", messages, trace=trace
            )
        self.assertEqual(result["content"], "Final answer")
        self.assertEqual(result["model"], "codex/native-other")
        events = self.events()
        initialized = next(e for e in events if e.get("method") == "initialize")
        self.assertFalse(
            initialized["params"].get("clientCapabilities", {}).get("terminal", False)
        )
        prompt = next(e for e in events if e.get("method") == "session/prompt")
        self.assertIn(
            json.dumps(messages, ensure_ascii=False),
            prompt["params"]["prompt"][0]["text"],
        )
        self.assertFalse(Path(events[0]["cwd"]).exists())
        self.assertEqual(trace.events[0]["auth_kind"], "account")
        self.assertEqual(trace.events[0]["kind"], "llm_call")
        self.assertEqual(trace.events[0]["transport"], "acp")

    async def test_auth_and_model_fail_before_generation(self) -> None:
        for scenario, model in (
            ("api_auth", "default"),
            ("no_auth", "default"),
            ("changed_auth", "default"),
            ("success", "missing"),
            ("wrong_model", "native-other"),
        ):
            with (
                self.subTest(scenario=scenario),
                self.launch(scenario),
                patch.object(acp_client, "AUTH_TIMEOUT_SECONDS", 0.05),
            ):
                self.log.unlink(missing_ok=True)
                with self.assertRaises(acp_client.AgentError):
                    await acp_client.query_model(f"codex/{model}", [])
                self.assertNotIn(
                    "session/prompt", [e.get("method") for e in self.events()]
                )

    async def test_incomplete_and_unsafe_results_are_errors(self) -> None:
        for scenario in (
            "empty",
            "truncated",
            "error",
            "permission",
            "wrong_session",
            "changed_model",
            "oversized",
        ):
            with (
                self.subTest(scenario=scenario),
                self.launch(scenario),
                patch.object(acp_client, "MAX_TEXT_BYTES", 1024),
            ):
                trace = TraceCollector()
                with self.assertRaises(acp_client.AgentError):
                    await acp_client.query_model("claude/default", [], trace=trace)
                self.assertNotIn("sensitive-vendor-detail", json.dumps(trace.to_dict()))

    async def test_expired_login_has_a_safe_actionable_error(self) -> None:
        with self.launch("auth_required"):
            with self.assertRaisesRegex(
                acp_client.AgentError, "Sign in using its native CLI"
            ):
                await acp_client.query_model("pi/provider/native", [])

    async def test_timeout_and_cancellation_kill_descendants(self) -> None:
        for cancel in (False, True):
            with self.subTest(cancel=cancel), self.launch("hang"):
                self.log.unlink(missing_ok=True)
                task = asyncio.create_task(
                    acp_client.query_model("codex/default", [], timeout=1)
                )
                for _ in range(200):
                    if self.log.exists() and any(
                        "child_pid" in e for e in self.events()
                    ):
                        break
                    await asyncio.sleep(0.005)
                else:
                    self.fail("Fake agent did not spawn its child")
                if cancel:
                    task.cancel()
                with self.assertRaises(
                    asyncio.CancelledError if cancel else acp_client.AgentError
                ):
                    await task
                pids = [
                    e[key]
                    for e in self.events()
                    for key in ("pid", "child_pid")
                    if key in e
                ]
                for pid in pids:
                    # A reparented zombie has exited and cannot continue consuming quota.
                    status = subprocess.run(
                        ["ps", "-o", "stat=", "-p", str(pid)],
                        capture_output=True,
                        text=True,
                    )
                    self.assertTrue(
                        not status.stdout.strip()
                        or status.stdout.strip().startswith("Z")
                    )

    async def test_no_api_fallback_and_independent_sessions(self) -> None:
        with (
            self.launch(),
            patch.object(model_client, "MODEL_BACKEND", "acp"),
            patch.object(
                model_client.openrouter,
                "query_model",
                new=AsyncMock(side_effect=AssertionError("API called")),
            ) as api,
        ):
            await asyncio.gather(
                *(model_client.query_model("codex/default", []) for _ in range(2))
            )
            api.assert_not_called()
        starts = [e for e in self.events() if "pid" in e]
        self.assertEqual(len({e["cwd"] for e in starts}), 2)
        with (
            self.launch("error"),
            patch.object(model_client, "MODEL_BACKEND", "acp"),
            patch.object(
                model_client.openrouter, "query_model", new=AsyncMock()
            ) as api,
        ):
            with self.assertRaises(acp_client.AgentError):
                await model_client.query_model("codex/default", [])
            api.assert_not_called()

    async def test_failed_stage_cancels_other_agents(self) -> None:
        cancelled = asyncio.Event()

        async def wait() -> None:
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()

        async def fail() -> None:
            await asyncio.sleep(0)
            raise acp_client.AgentError("Quota exhausted")

        with self.assertRaises(acp_client.AgentError):
            await council._gather_model_calls([wait(), fail()])
        self.assertTrue(cancelled.is_set())

    async def test_acp_synthesis_keeps_complete_evidence(self) -> None:
        artifacts = {
            "question": "q",
            "guidance": {},
            "evidence": {"responses": "FULL" * 30000},
        }
        response = {
            "content": "Answer",
            "model": "codex/native",
            "usage": None,
            "finish_reason": "stop",
        }
        with (
            patch.object(agent_synthesis, "SYNTHESIS_AGENT_DIR", self.directory.name),
            patch.object(agent_synthesis, "CHAIRMAN_MODEL", "codex/default"),
            patch.object(
                model_client, "query_model", new=AsyncMock(return_value=response)
            ) as query,
        ):
            result = await agent_synthesis.synthesize("acp", artifacts)
        self.assertEqual(json.loads(query.call_args.args[1][1]["content"]), artifacts)
        self.assertEqual(Path(result["workspace"], "result.md").read_text(), "Answer")

    async def test_all_council_stages_and_helpers_use_acp(self) -> None:
        calls = []

        async def answer(model: str, messages: list, **kwargs: Any) -> dict:
            stage = kwargs.get("trace_stage") or "stage3"
            calls.append(stage)
            content = {
                "stage2": "FINAL RANKING:\n1. Response A\n2. Response B",
                "negative_eval": '{"self_score": 7, "peer_scores": {"Response A": 8, "Response B": 8}}',
                "skill_selection": '{"selected": [1]}',
                "skill_references": '{"selected": [1]}',
                "prompt_refinement": '{"intent": "Test", "refined_prompt": "Question", "clarifying_questions": []}',
            }.get(stage, "Answer")
            return {
                "content": content,
                "model": model,
                "finish_reason": "stop",
                "usage": None,
            }

        with ExitStack() as patches:
            for module, key, value in (
                (model_client, "MODEL_BACKEND", "acp"),
                (council, "COUNCIL_MODELS", ["codex/default", "pi/provider/native"]),
                (council, "NEGATIVE_MODE_ENABLED", True),
                (council, "DELIBERATION_ENABLED", True),
                (council, "SKILLS_TYPESAFE_ENABLED", False),
                (council, "SYNTHESIS_BACKEND", "acp"),
                (agent_synthesis, "SYNTHESIS_AGENT_DIR", self.directory.name),
                (agent_synthesis, "CHAIRMAN_MODEL", "codex/default"),
            ):
                patches.enter_context(patch.object(module, key, value))
            for key in (
                "CHAIRMAN_MODEL",
                "TITLE_MODEL",
                "PROMPT_REFINEMENT_MODEL",
                "SKILLS_REFERENCE_SELECTION_MODEL",
                "SKILLS_AUTO_SELECT_MODEL",
            ):
                patches.enter_context(patch.object(council, key, "codex/default"))
            patches.enter_context(
                patch.object(acp_client, "query_model", side_effect=answer)
            )
            api = patches.enter_context(
                patch.object(model_client.openrouter, "query_model", new=AsyncMock())
            )
            paid_router = patches.enter_context(
                patch.object(council, "select_relevant", new=AsyncMock())
            )
            result = await council.run_full_council("Question")
            self.assertEqual(result[3]["response"], "Answer")
            await council.generate_conversation_title("Question")
            await council.generate_prompt_refinement("Question")
            skill = Skill(
                "skill", "Skill", "description", 1, True, "", "", None, "", 10
            )
            await council.select_skills("Question", [skill])
            candidate = ReferenceCandidate(
                1,
                "skill",
                "description",
                "ref.md",
                "section",
                "Heading",
                "Summary",
                10,
                [],
                "Summary",
            )
            await council.select_skill_references("Question", [candidate])
            api.assert_not_called()
            paid_router.assert_not_called()
        self.assertEqual(
            set(calls),
            {
                "stage1",
                "negative_prompt",
                "negative_eval",
                "stage2",
                "deliberation",
                "stage3",
                "title",
                "prompt_refinement",
                "skill_selection",
                "skill_references",
            },
        )


class ACPConfigurationTests(unittest.TestCase):
    def test_child_environment_and_tool_settings(self) -> None:
        secrets = {
            key: "secret"
            for key in (
                "API_AUTH_KEY",
                "OPENROUTER_API_KEY",
                "OPENAI_API_KEY",
                "CODEX_API_KEY",
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "AWS_SECRET_ACCESS_KEY",
                "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_VERTEX",
                "DEFAULT_AUTH_REQUEST",
            )
        }
        with (
            patch.dict(os.environ, secrets),
            patch.object(Path, "is_file", return_value=True),
        ):
            for agent in ("codex", "claude", "pi"):
                command, env, meta = acp_client.launch_settings(
                    agent, "provider/native"
                )
                self.assertFalse(set(env).intersection(secrets))
                if agent == "claude":
                    self.assertEqual(meta["claudeCode"]["options"]["tools"], [])
                    self.assertEqual(
                        meta["claudeCode"]["options"]["settingSources"], []
                    )
                elif agent == "pi":
                    self.assertEqual(command[-1], "provider/native")
                    self.assertEqual(env["PI_OFFLINE"], "1")

    def test_pi_requires_explicit_provider_and_model(self) -> None:
        with patch.object(Path, "is_file", return_value=True):
            for model in ("default", "provider/", "/native", "provider/default"):
                with (
                    self.subTest(model=model),
                    self.assertRaises(acp_client.AgentError),
                ):
                    acp_client.launch_settings("pi", model)

    def test_pi_config_accepts_explicit_models_and_rejects_defaults(self) -> None:
        script = """
import os, dotenv
dotenv.load_dotenv = lambda: None
for key in list(os.environ):
    if key.endswith('_MODEL') or key == 'COUNCIL_MODELS':
        del os.environ[key]
os.environ.update(MODEL_BACKEND='acp', API_AUTH_KEY='test', COUNCIL_MODELS='pi/provider/native', CHAIRMAN_MODEL='pi/provider/native')
from backend import config
assert config.COUNCIL_MODELS == ['pi/provider/native']
assert config.TITLE_MODEL == config.CHAIRMAN_MODEL == 'pi/provider/native'
"""
        for model, success in (
            ("pi/provider/native", True),
            ("pi/default", False),
            ("pi/provider/default", False),
        ):
            result = subprocess.run(
                [sys.executable, "-c", script.replace("pi/provider/native", model)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode == 0, success, result.stderr)

    def test_acp_config_starts_without_api_key_and_disables_paid_helpers(self) -> None:
        script = """
import os, dotenv
dotenv.load_dotenv = lambda: None
for key in list(os.environ):
    if key.endswith('_MODEL') or key in ('COUNCIL_MODELS', 'OPENROUTER_API_KEY'):
        del os.environ[key]
os.environ.update(MODEL_BACKEND='acp', API_AUTH_KEY='test', SKILLS_TYPESAFE_ENABLED='true', SYNTHESIS_BACKEND='openrouter')
from backend import config
assert config.COUNCIL_MODELS == ['codex/default', 'claude/default']
assert config.SYNTHESIS_BACKEND == 'acp'
assert config.CHAIRMAN_MODEL == config.TITLE_MODEL == 'codex/default'
assert not config.SKILLS_TYPESAFE_ENABLED
"""
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
