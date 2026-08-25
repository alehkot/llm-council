"""Command-line utilities for maintaining LLM Council data."""

import argparse

from .config import DATA_DIR
from .storage import clear_conversations


def _clear_conversations(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing to delete conversations. Re-run with --yes to confirm.")
        return 2

    deleted = clear_conversations()
    print(f"Deleted {deleted} conversation file(s) from {DATA_DIR}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM Council maintenance CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    clear_parser = subparsers.add_parser(
        "clear-conversations",
        help="Delete all stored conversations.",
    )
    clear_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deletion of all conversation files.",
    )
    clear_parser.set_defaults(func=_clear_conversations)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
