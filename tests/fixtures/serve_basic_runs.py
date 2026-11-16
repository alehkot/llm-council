"""Disposable authenticated backend for the Rust command integration test."""

import os
import socket
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["API_AUTH_KEY"] = "synthetic-cli-integration"

import uvicorn
from test_basic_runs import BasicRunsTests

from backend import basic_runs
from backend.main import app

fixture = BasicRunsTests()
fixture.setUp()


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await basic_runs.shutdown()


app.router.lifespan_context = lifespan
server = uvicorn.Server(uvicorn.Config(app, log_level="error"))


def stop_at_stdin_eof():
    # The test's stdin pipe closes when it finishes or dies. Killing `uv run`
    # alone would leave this process running with the test's stderr open.
    while os.read(0, 1024):
        pass
    server.should_exit = True


threading.Thread(target=stop_at_stdin_eof, daemon=True).start()
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
# Listen before printing the port: connections then wait for uvicorn instead of
# being refused (Linux) or dropped (macOS) while it starts.
sock.listen()
print(sock.getsockname()[1], flush=True)
try:
    server.run(sockets=[sock])
finally:
    fixture.doCleanups()
