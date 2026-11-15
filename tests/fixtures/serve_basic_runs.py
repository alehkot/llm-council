"""Disposable authenticated backend for the Rust command integration test."""

import os
import socket
import sys
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
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1], flush=True)
try:
    uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[sock])
finally:
    fixture.doCleanups()
