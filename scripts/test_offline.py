"""Run backend tests with outbound networking forbidden, even if errors are caught."""

import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["OPENROUTER_API_KEY"] = "test-openrouter-key"
os.environ["API_AUTH_KEY"] = "test-api-key"


def main() -> int:
    attempts = []
    current = ["test discovery"]
    real_connect = socket.socket.connect

    class Result(unittest.TextTestResult):
        def startTest(self, test: unittest.TestCase) -> None:
            current[0] = test.id()
            super().startTest(test)

    def connect(sock: socket.socket, address: object) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            attempts.append(current[0])
            raise AssertionError("Outbound networking is forbidden in offline tests")
        return real_connect(sock, address)

    async def http(*args: object, **kwargs: object) -> httpx.Response:
        attempts.append(current[0])
        raise AssertionError("Use httpx.MockTransport in offline tests")

    with (
        patch.object(socket.socket, "connect", connect),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", http),
    ):
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(resultclass=Result).run(suite)
    if attempts:
        print("Unmocked network attempts:", ", ".join(sorted(set(attempts))))
    return int(not result.wasSuccessful() or bool(attempts))


if __name__ == "__main__":
    raise SystemExit(main())
