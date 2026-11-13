"""Offline real controlling-terminal prompt checks. No backend/credentials."""

import argparse
import fcntl
import os
import pty
import select
import signal
import subprocess
import tempfile
import termios
import time
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--deps-dir", required=True, type=Path)
parser.add_argument("--rustc", default="rustc")
args = parser.parse_args()
cli = Path(__file__).resolve().parents[2]
lib = max(args.deps_dir.rglob("libcouncil_cli-*.rlib"), key=lambda p: p.stat().st_mtime)
assert all(
    p.stat().st_mtime <= lib.stat().st_mtime for p in (cli / "src").glob("*.rs")
), "Re-run cargo tests first"
serde = max(
    args.deps_dir.rglob("libserde_json-*.rlib"), key=lambda p: p.stat().st_mtime
)
with tempfile.TemporaryDirectory(prefix="council-prompt-check-") as directory:
    probe = str(Path(directory) / "probe")
    dependency_paths = sorted(
        {
            p.parent
            for p in args.deps_dir.rglob("*")
            if p.suffix in (".rlib", ".so", ".dylib")
        }
    )
    search_paths = [
        flag for path in dependency_paths for flag in ("-L", f"dependency={path}")
    ]
    subprocess.run(
        [
            args.rustc,
            "--edition",
            "2021",
            str(Path(__file__).with_name("prompt_probe.rs")),
            "--extern",
            f"council_cli={lib}",
            "--extern",
            f"serde_json={serde}",
            "-o",
            probe,
            *search_paths,
        ],
        check=True,
        timeout=60,
    )
    for mode, data in [
        ("answer", "answer\n1\n1\n  Привет  \nno\n".encode()),
        ("leave", b"leave\n"),
        ("unavailable", b"\x04"),
        ("interrupt", b"\x03"),
        ("oversized", b"answer\n1\n1\n" + b"x" * 5000 + b"\n"),
    ]:
        master, slave = pty.openpty()
        original = termios.tcgetattr(slave)

        def session():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        proc = subprocess.Popen(
            [probe, mode],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=session,
            pass_fds=(slave,),
        )
        try:
            output = b""
            deadline = time.monotonic() + 5
            while b"leave: " not in output:
                assert time.monotonic() < deadline, "prompt timeout"
                if select.select([master], [], [], 0.1)[0]:
                    output += os.read(master, 65536)
            proc.stdin.write(b"wrong stdin\n")
            proc.stdin.flush()
            os.write(master, data)
            stdout, stderr = proc.communicate(timeout=5)
            if mode == "interrupt":
                assert proc.returncode == -signal.SIGINT, (mode, proc.returncode)
                assert stdout == b"", (mode, stdout)
            else:
                assert proc.returncode == 0, (mode, stderr)
                assert stdout == b"OK\n", (mode, stdout)
            assert termios.tcgetattr(slave) == original, "changed terminal flags"
            print(f"PASS {mode}: real /dev/tty, piped stdin ignored, modes unchanged")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(master)
            os.close(slave)
    proc = subprocess.run(
        [probe, "unavailable"],
        input=b"answer\n",
        capture_output=True,
        start_new_session=True,
        timeout=5,
    )
    assert proc.returncode == 0 and proc.stdout == b"OK\n", proc.stderr
    print("PASS unavailable: no controlling terminal leaves input pending")
