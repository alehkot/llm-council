"""Offline synthetic-key PTY probe; no Keychain/backend access, 5s per case."""

import argparse
import fcntl
import os
import pty
import select
import subprocess
import tempfile
import termios
import time
from pathlib import Path

CLI = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--deps-dir", type=Path, default=CLI / "target/debug/deps")
parser.add_argument("--rustc", default="rustc")
args = parser.parse_args()
libraries = sorted(
    args.deps_dir.glob("libcouncil_cli-*.rlib"), key=lambda p: p.stat().st_mtime
)
if not libraries:
    parser.error(
        "Run cargo test --locked --tests first; no test-built library was found."
    )
library = libraries[-1]
inputs = [*CLI.joinpath("src").rglob("*.rs"), CLI / "Cargo.toml", CLI / "Cargo.lock"]
if any(p.stat().st_mtime > library.stat().st_mtime for p in inputs):
    parser.error(
        "Library predates sources; rerun cargo test --locked --tests before this check."
    )
workspace = tempfile.TemporaryDirectory(prefix="council-terminal-check-")
PROBE = str(Path(workspace.name) / "terminal-probe")
subprocess.run(
    [
        args.rustc,
        "--edition",
        "2021",
        str(Path(__file__).with_name("terminal_probe.rs")),
        "--extern",
        f"council_cli={library}",
        "-L",
        f"dependency={args.deps_dir}",
        "-o",
        PROBE,
    ],
    check=True,
    timeout=60,
)


def case(name, data, expected):
    master, slave = pty.openpty()
    before = termios.tcgetattr(slave)

    def session():
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        [PROBE, expected],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=session,
        pass_fds=(slave,),
    )
    output = b""
    deadline = time.monotonic() + 5
    try:
        while b"Backend API key (hidden): " not in output:
            assert time.monotonic() < deadline, f"{name}: prompt timeout"
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 65536)
        hidden = termios.tcgetattr(slave)
        assert not hidden[3] & termios.ECHO, name
        # A piped stdin credential must never be consumed by the TTY prompt.
        proc.stdin.write(b"wrong-piped-key\n")
        proc.stdin.flush()
        os.write(master, data)
        stdout, stderr = proc.communicate(timeout=5)
        while select.select([master], [], [], 0.01)[0]:
            output += os.read(master, 65536)
        assert proc.returncode == 0, (name, proc.returncode, stderr)
        assert stdout == b"OK\n", (name, stdout)
        assert b"offline-test-key" not in output, name
        assert b"wrong-piped-key" not in output, name
        assert termios.tcgetattr(slave) == before, (
            f"{name}: terminal flags not restored"
        )
        assert not select.select([slave], [], [], 0.01)[0], (
            f"{name}: unconsumed secret input"
        )
        print(
            f"PASS {name}: TTY bypasses stdin; no key echo; exact terminal flags restored"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(master)
        os.close(slave)


for name, data, expected in [
    ("enter", b"offline-test-key\n", "valid"),
    ("CRLF", b"offline-test-key\r\n", "valid"),
    ("backspace", b"offline-test-keX\x7fy\n", "valid"),
    ("clear-line", b"discard\x15offline-test-key\n", "valid"),
    ("Ctrl-C partial", b"partial\x03", "cancel"),
    ("Ctrl-D partial", b"partial\x04", "cancel"),
    ("Ctrl-D empty", b"\x04", "cancel"),
    ("non-ASCII", "é\n".encode(), "invalid"),
    ("Ctrl-S", b"\x13", "invalid"),
    ("Ctrl-Q", b"\x11", "invalid"),
    ("oversized", b"x" * 4097 + b"\n", "invalid"),
]:
    case(name, data, expected)
p = subprocess.run(
    [PROBE, "unavailable"],
    input=b"piped-key\n",
    capture_output=True,
    start_new_session=True,
    timeout=5,
)
assert p.returncode == 0 and p.stdout == b"OK\n", (p.returncode, p.stdout, p.stderr)
print("PASS no controlling TTY: refuses piped stdin")

workspace.cleanup()
