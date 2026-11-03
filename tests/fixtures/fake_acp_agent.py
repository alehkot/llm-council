"""Small stdio ACP peer used to test transport behavior without a vendor account."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def main() -> None:
    scenario, log_name = sys.argv[1:]
    log = Path(log_name)
    session_id = str(os.getpid())
    current_model = "native-default"

    def send(value: dict[str, Any]) -> None:
        print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)

    def record(value: dict[str, Any]) -> None:
        with log.open("a") as output:
            output.write(json.dumps(value) + "\n")

    def auth(kind: str) -> None:
        send(
            {"method": "_auth/status_update", "params": {"authStatus": {"kind": kind}}}
        )

    def options() -> list[dict[str, Any]]:
        return [
            {
                "id": "model",
                "name": "Model",
                "category": "model",
                "type": "select",
                "currentValue": current_model,
                "options": [
                    {"value": item, "name": item}
                    for item in ("native-default", "native-other", "provider/native")
                ],
            }
        ]

    record({"pid": os.getpid(), "cwd": os.getcwd(), "env_keys": list(os.environ)})
    for line in sys.stdin:
        request = json.loads(line)
        record(request)
        method = request.get("method")
        params = request.get("params", {})
        result: dict[str, Any] = {}
        if method == "initialize":
            send(
                {
                    "id": request["id"],
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {"_meta": {"authStatus": {}}},
                    },
                }
            )
            if scenario != "no_auth":
                auth("api_key" if scenario == "api_auth" else "account")
            continue
        if method == "session/new":
            if scenario == "changed_auth":
                auth("gateway")
            result = {"sessionId": session_id, "configOptions": options()}
        elif method == "session/set_config_option":
            if scenario != "wrong_model":
                current_model = params["value"]
            result = {"configOptions": options()}
        elif method == "session/prompt":
            if scenario == "changed_model":
                current_model = "native-other"
                send(
                    {
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "config_option_update",
                                "configOptions": options(),
                            },
                        },
                    }
                )
            if scenario == "hang":
                child = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"]
                )
                record({"child_pid": child.pid})
                time.sleep(60)
            if scenario == "permission":
                send(
                    {
                        "id": "permission",
                        "method": "session/request_permission",
                        "params": {
                            "sessionId": session_id,
                            "options": [],
                            "toolCall": {
                                "toolCallId": "tool",
                                "title": "Execute command",
                            },
                        },
                    }
                )
                continue
            if scenario != "empty":
                send(
                    {
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "messageId": "progress",
                                "content": {
                                    "type": "text",
                                    "text": "I will think about this.",
                                },
                            },
                        },
                    }
                )
                send(
                    {
                        "method": "session/update",
                        "params": {
                            "sessionId": "wrong"
                            if scenario == "wrong_session"
                            else session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "messageId": "answer",
                                "content": {
                                    "type": "text",
                                    "text": "x" * 2048
                                    if scenario == "oversized"
                                    else "Final answer",
                                },
                            },
                        },
                    }
                )
            if scenario in {"error", "auth_required"}:
                send(
                    {
                        "id": request["id"],
                        "error": {
                            "code": -32000 if scenario == "auth_required" else -32603,
                            "message": "sensitive-vendor-detail",
                        },
                    }
                )
                continue
            result = {
                "stopReason": "max_tokens" if scenario == "truncated" else "end_turn"
            }
        if "id" in request and method:
            send({"id": request["id"], "result": result})


if __name__ == "__main__":
    main()
