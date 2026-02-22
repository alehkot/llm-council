#!/usr/bin/env python3
"""Generate an API auth key and update the repo .env file."""

from __future__ import annotations

import re
import secrets
from pathlib import Path

ENV_KEYS = ("API_AUTH_KEY", "VITE_API_KEY")
KEY_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def generate_key() -> str:
    return secrets.token_urlsafe(32)


def update_env_file(path: Path, key: str) -> None:
    updates = {name: key for name in ENV_KEYS}
    if path.exists():
        lines = path.read_text().splitlines(keepends=True)
    else:
        lines = []

    found = {name: False for name in updates}
    new_lines = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            new_lines.append(line)
            continue
        match = KEY_PATTERN.match(line)
        if match:
            name = match.group(1)
            if name in updates:
                new_lines.append(f"{name}={updates[name]}\n")
                found[name] = True
                continue
        new_lines.append(line)

    missing = [name for name, seen in found.items() if not seen]
    if missing:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] = f"{new_lines[-1]}\n"
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("\n")
        new_lines.append("# Local API auth\n")
        for name in missing:
            new_lines.append(f"{name}={updates[name]}\n")

    path.write_text("".join(new_lines))


def main() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    key = generate_key()
    update_env_file(env_path, key)
    print(f"Updated {env_path} with a new API auth key.")


if __name__ == "__main__":
    main()
