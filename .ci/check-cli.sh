#!/usr/bin/env bash
# Focused Rust checks; no local full application/release build.
set -euo pipefail
cd "$(dirname "$0")/.."
cargo fmt --manifest-path cli/Cargo.toml --check
cargo clippy --manifest-path cli/Cargo.toml --locked --all-targets -- -D warnings
cargo test --manifest-path cli/Cargo.toml --locked
