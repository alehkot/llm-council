# Offline receipt storage draft

Starting development and main: `2db18d88b696f7efb66e59e4fc3914268be2396b`.
This is an isolated draft, not a publication receipt or completed plan milestone.
The experimental Unix-only primitive retains serialized typed request bytes and
validated origin, fails on conflicting UUID reuse, serializes cooperating writers,
checks owner/mode bits (not ACLs), and atomically replaces pending content only after caller
supplies a matching acknowledgment. Its directory descriptor anchors accesses;
no commands, network calls or API capability are enabled.

Focused verification in this checkout: eleven offline integration tests pass
(five consecutive runs), and one injected synchronization-failure unit test
passes. Rustfmt and Clippy passed. These synthetic tests cover process lock,
concurrent callers, byte preservation, symlink/hard-link rejection, renamed
ancestor, partial record and acknowledgment remnants, privacy-safe debug output,
and selected file/directory sync failures. They do not verify physical power-loss,
filesystem adversaries with the same user account, or real backend semantics.
Native macOS, stronger device-cache flush behavior, Windows, live providers and
full builds remain unrun. CLI API remains 0; no submission is enabled.

A fresh initial write interrupted before completion leaves a partial record, which
fails closed and needs manual recovery. Logical deletion is not physical erasure.
The component requires a private directory provisioned by future integration and
does not protect user-entered secrets in questions. Native target-platform crash
and storage semantics, ACL validation, stable trusted root/ancestor provisioning
and restart recovery after a directory rename, error recovery UX, ownership
and policy, acknowledgement validation and exact network integration must be
resolved before live use.
