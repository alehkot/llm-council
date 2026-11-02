# Offline controlling-terminal verification

Run from the repository root on a Unix machine with Python 3, Rust, and PTYs:

```sh
cargo test --manifest-path cli/Cargo.toml --locked --tests
python cli/tests/manual/check_terminal.py
```

The script links a tiny probe against the **existing test-built library**, not a
full release/application build. Use `--deps-dir PATH` for a custom Cargo target
directory and `--rustc PATH` if Rust is outside PATH. It refuses a missing library
or one older than the source/configuration inputs. Rerun Cargo tests after edits.

Twelve synthetic-key cases exercise actual controlling-terminal input while stdin
is a pipe: Enter, CRLF, backspace, clear-line, Ctrl-C, Ctrl-D with/without partial
input, invalid non-ASCII, Ctrl-S/Ctrl-Q, oversized entry, and no controlling terminal. Checks
cover no key echo, exact terminal-flag restoration, flushed unread input, and no
fallback to piped stdin. Pure unit tests additionally check EOF before newline.
Each subprocess has a timeout and is cleaned up on failure.

This is not a Keychain or backend test: no credential is stored, no network
request occurs, and no real secret is used. Linux success does not establish
macOS terminal or native Keychain behavior. macOS verification remains required
before claiming platform acceptance. Terminal restoration covers normal/error
returns and typed Ctrl-C; external process termination (for example SIGTERM or SIGKILL) can bypass
restoration code. There is no universal signal-restoration guarantee.
