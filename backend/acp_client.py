"""Isolated ACP text calls with explicit authentication routes. No fallback."""

import asyncio
import contextlib
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, RequestError, connect_to_agent, text_block
from acp.schema import (
    AgentMessageChunk,
    ClientCapabilities,
    ConfigOptionUpdate,
    Implementation,
    RequestPermissionResponse,
    TextContentBlock,
)

from .trace import TraceCollector

ADAPTER_DIR = Path(__file__).resolve().parents[1] / "agent-adapters"
MAX_TEXT_BYTES = 10_000_000
AUTH_TIMEOUT_SECONDS = 20


class AgentError(RuntimeError):
    """Safe, user-visible failure; never includes raw agent logs or credentials."""


def launch_settings(
    agent: str, native_model: str
) -> tuple[list[str], dict[str, str], dict[str, Any]]:
    if os.name != "posix":
        raise AgentError("ACP process isolation currently requires macOS or Linux.")
    binaries = {
        "codex": "codex-acp",
        "claude": "claude-agent-acp",
        "pi": "pi",
        "agy": "agy_acp_server.par",
    }
    if agent not in binaries:
        raise AgentError(
            f"Unsupported ACP agent: {agent}. Use codex, claude, pi, or agy."
        )
    runtime_dir = (
        Path(
            os.getenv("ANTIGRAVITY_ACP_DIR")
            or ADAPTER_DIR.parent / "data" / "agent-runtimes" / "antigravity"
        )
        .expanduser()
        .resolve()
    )
    executable = (
        runtime_dir if agent == "agy" else ADAPTER_DIR / "node_modules" / ".bin"
    ) / binaries[agent]
    if not executable.is_file():
        if agent == "agy":
            raise AgentError(
                "Install Google's official Antigravity ACP 1.4.5 runtime in data/agent-runtimes/antigravity, or set ANTIGRAVITY_ACP_DIR."
            )
        raise AgentError(
            "ACP adapters are missing. Run npm ci --prefix agent-adapters."
        )
    # An allowlist also excludes cloud credentials, custom gateways, and agent hooks.
    env = {
        key: os.environ[key]
        for key in (
            "HOME",
            "PATH",
            "USER",
            "LOGNAME",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "SYSTEMROOT",
            "CODEX_HOME",
            "CLAUDE_CONFIG_DIR",
            "PI_CODING_AGENT_DIR",
        )
        if key in os.environ
    }
    meta: dict[str, Any] = {}
    if agent == "agy":
        # shortcut: Antigravity reuses macOS keychain login; add file-profile support before enabling other OSes.
        if sys.platform != "darwin":
            raise AgentError(
                "Antigravity currently requires native macOS account storage."
            )
        helper = runtime_dir / "localharness_external"
        if not helper.is_file():
            raise AgentError(
                "Antigravity's matching localharness_external helper is missing."
            )
        env["ANTIGRAVITY_HARNESS_PATH"] = str(helper)
        meta = {"agy": {"enabledTools": []}}
    elif agent == "codex":
        env.update(
            {
                "CODEX_PATH": str(ADAPTER_DIR / "codex-safe.cjs"),
                "MODEL_PROVIDER": "openai",
                "INITIAL_AGENT_MODE": "read-only",
                "NO_BROWSER": "1",
                "CODEX_CONFIG": json.dumps(
                    {
                        "model_provider": "openai",
                        "forced_login_method": "chatgpt",
                        "sandbox_mode": "read-only",
                        "approval_policy": "never",
                        "web_search": "disabled",
                    }
                ),
            }
        )
    elif agent == "claude":
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        meta = {
            "claudeCode": {
                "options": {
                    "tools": [],
                    "settingSources": [],
                    "promptSuggestions": False,
                    "extraArgs": {"safe-mode": "", "no-session-persistence": ""},
                }
            }
        }
    else:
        provider, separator, model = native_model.partition("/")
        if not provider or not separator or not model or model == "default":
            raise AgentError(
                "Pi requires pi/<provider>/<native-model-id>; defaults are refused."
            )
        env["PI_OFFLINE"] = "1"  # Use pinned model catalogs, without custom routes.
        return ["node", str(ADAPTER_DIR / "pi-acp.mjs"), native_model], env, meta
    return [str(executable)], env, meta


class TextClient:
    def __init__(self, expected_auth_kind: str = "account") -> None:
        self.chunks: list[str] = []
        self.size = 0
        self.auth_kind: str | None = None
        self.expected_auth_kind = expected_auth_kind
        self.auth_ready = asyncio.Event()
        self.failed = asyncio.Event()
        self.error: str | None = None
        self.session_id: str | None = None
        self.message_id: str | None = None
        self.collecting = False
        self.expected_model: str | None = None

    def fail(self, message: str) -> None:
        self.error = message
        self.failed.set()

    async def request_permission(self, **kwargs: Any) -> RequestPermissionResponse:
        self.fail("Agent requested a tool permission during a text-only council call.")
        return RequestPermissionResponse(outcome={"outcome": "cancelled"})

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if self.session_id and session_id != self.session_id:
            self.fail("Agent sent a response for a different session.")
            return
        if self.expected_model and isinstance(update, ConfigOptionUpdate):
            for option in update.config_options:
                if (
                    option.category == "model"
                    and option.current_value != self.expected_model
                ):
                    self.fail(
                        "Agent changed the selected model during the call; fallback is refused."
                    )
        if (
            self.collecting
            and isinstance(update, AgentMessageChunk)
            and isinstance(update.content, TextContentBlock)
        ):
            self.size += len(update.content.text.encode())
            if self.size > MAX_TEXT_BYTES:
                self.fail("Agent response exceeded the output limit.")
                return
            if update.message_id and update.message_id != self.message_id:
                # Earlier assistant messages may be progress reports, not the answer.
                self.chunks.clear()
                self.message_id = update.message_id
            self.chunks.append(update.content.text)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "auth/status_update":
            status = params.get("authStatus", {})
            self.auth_kind = status.get("kind")
            self.auth_ready.set()
            if self.auth_kind != self.expected_auth_kind:
                self.fail(
                    "Agent authentication does not match the selected route; no fallback is allowed."
                )


async def _select_model(connection: Any, session: Any, native_model: str) -> str:
    options = session.config_options or []
    model_option = next(
        (option for option in options if option.category == "model"), None
    )
    if model_option is not None:
        if native_model == "default":
            return model_option.current_value
        values = []
        for option in model_option.options:
            values.extend(getattr(option, "options", [option]))
        if native_model not in {option.value for option in values}:
            raise AgentError(
                f"Agent does not advertise model {native_model!r}; no substitute was selected."
            )
        result = await connection.set_config_option(
            session_id=session.session_id,
            config_id=model_option.id,
            value=native_model,
        )
        selected = next(
            (
                option
                for option in result.config_options
                if option.id == model_option.id
            ),
            None,
        )
        if selected is None or selected.current_value != native_model:
            raise AgentError("Agent did not confirm the requested model.")
        return native_model
    raise AgentError(
        "Agent did not report model configuration. Install the pinned adapters."
    )


async def _run(agent: str, native_model: str, prompt: str) -> dict[str, Any]:
    command, env, meta = launch_settings(agent, native_model)
    client = TextClient(
        "api_key"
        if agent == "pi" and native_model.startswith("openrouter/")
        else "account"
    )
    process = connection = stderr_task = failure_task = prompt_task = None
    with tempfile.TemporaryDirectory(prefix="llm-council-acp-") as workspace:
        try:
            if agent == "agy":
                profile = Path(workspace) / "profile"
                for relative, settings in {
                    "antigravity-acp/settings.json": {
                        "auth": {"type": "oauth-personal"}
                    },
                    "config/config.json": {"userSettings": {"useAiCredits": False}},
                    "antigravity-cli/settings.json": {"useG1Credits": False},
                }.items():
                    path = profile / relative
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.write_text(json.dumps(settings))
                    path.chmod(0o600)
                env["GEMINI_HOME"] = str(profile)
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workspace,
                env=env,
                start_new_session=True,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_TEXT_BYTES,
            )

            async def drain_stderr() -> None:
                # Do not retain vendor logs: they can contain credentials and private context.
                while await process.stderr.read(65536):
                    pass

            stderr_task = asyncio.create_task(drain_stderr())
            connection = connect_to_agent(client, process.stdin, process.stdout)
            initialized = await connection.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(name="llm-council", version="0.1.0"),
            )
            if agent == "agy":
                info = initialized.agent_info
                if (
                    not info
                    or info.name != "antigravity-acp"
                    or info.version != "1.4.5"
                    or "oauth-personal"
                    not in {method.id for method in initialized.auth_methods}
                ):
                    raise AgentError(
                        "Expected Google's official Antigravity ACP 1.4.5 runtime with personal-account authentication."
                    )
            else:
                if "authStatus" not in (
                    initialized.agent_capabilities.field_meta or {}
                ):
                    raise AgentError(
                        "Adapter cannot verify authentication. Install the pinned adapters."
                    )
                try:
                    await asyncio.wait_for(
                        client.auth_ready.wait(), AUTH_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError as exc:
                    raise AgentError(
                        "Agent did not report authentication. Sign in using its native CLI first."
                    ) from exc
            if client.error:
                raise AgentError(client.error)
            session = await connection.new_session(
                cwd=workspace, mcp_servers=[], **meta
            )
            client.session_id = session.session_id
            if agent == "agy":
                tier = (session.field_meta or {}).get("antigravity", {}).get("tier", {})
                if not tier.get("id") or tier.get("name") not in {
                    "Google AI Pro",
                    "Google AI Ultra",
                }:
                    raise AgentError(
                        "Antigravity did not confirm a Google AI Pro or Ultra subscription."
                    )
                client.auth_kind = "account"
            if client.error:
                raise AgentError(client.error)
            actual_model = await _select_model(connection, session, native_model)
            client.expected_model = actual_model
            if client.error:
                raise AgentError(client.error)
            client.collecting = True
            failure_task = asyncio.create_task(client.failed.wait())
            prompt_task = asyncio.create_task(
                connection.prompt(
                    session_id=session.session_id,
                    prompt=[text_block(prompt)],
                )
            )
            await asyncio.wait(
                [prompt_task, failure_task], return_when=asyncio.FIRST_COMPLETED
            )
            if client.error:
                raise AgentError(client.error)
            result = await prompt_task
            content = "".join(client.chunks).strip()
            if result.stop_reason != "end_turn" or not content:
                raise AgentError(
                    f"Agent did not complete an answer (stop reason: {result.stop_reason})."
                )
            return {
                "content": content,
                "finish_reason": "stop",
                "usage": None,
                "model": f"{agent}/{actual_model}",
                "auth_kind": client.auth_kind,
            }
        finally:
            if (
                connection
                and client.session_id
                and prompt_task
                and not prompt_task.done()
            ):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        connection.cancel(session_id=client.session_id), 1
                    )
            # Kill the whole group even when the adapter exited before its children.
            if process:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            for task in (prompt_task, failure_task, stderr_task):
                if task:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            if connection:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(connection.close(), 2)


async def query_model(
    model: str,
    messages: list[dict[str, str]],
    timeout: float = 120.0,
    trace: TraceCollector | None = None,
    trace_stage: str | None = None,
    trace_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    agent, separator, native_model = model.partition("/")
    if not separator or not native_model:
        raise AgentError(
            "ACP model IDs must use agent/model, for example codex/default."
        )
    # JSON preserves roles, boundaries, Unicode, and all conversation turns verbatim.
    prompt = (
        "Answer the conversation below. Apply its system instructions and answer the last user turn. "
        "Use only the supplied context; do not use tools, access files, or take external actions. "
        "Return only the requested answer, without progress reports.\n\n"
        + json.dumps(messages, ensure_ascii=False)
    )
    if len(prompt.encode()) > MAX_TEXT_BYTES:
        raise AgentError(
            "Council prompt exceeds the ACP input limit; no content was silently removed."
        )
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(_run(agent, native_model, prompt), timeout)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if isinstance(exc, AgentError):
            error = str(exc)
        elif isinstance(exc, RequestError) and exc.code == -32000:
            error = "Agent login is missing or expired. Sign in using its native CLI and retry."
        elif isinstance(exc, asyncio.TimeoutError):
            error = "Agent call timed out."
        else:
            error = "Agent call failed. Check the native CLI login, quota, and pinned adapter installation."
        if trace:
            trace.add_event(
                "llm_call",
                stage=trace_stage,
                model=model,
                transport="acp",
                context=trace_context,
                timing={
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2)
                },
                error=error,
            )
        raise AgentError(f"{model}: {error}") from None
    if trace:
        trace.add_event(
            "llm_call",
            stage=trace_stage,
            model=model,
            transport="acp",
            actual_model=result["model"],
            auth_kind=result["auth_kind"],
            context=trace_context,
            timing={"duration_ms": round((time.perf_counter() - started) * 1000, 2)},
        )
    return result
