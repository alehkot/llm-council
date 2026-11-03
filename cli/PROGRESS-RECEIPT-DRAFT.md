# Offline receipt storage draft

Starting development and main: `2db18d88b696f7efb66e59e4fc3914268be2396b`.
This experimental primitive is not a completed plan milestone.
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

## Publication checkpoint (2026-11-03)

Local implementation commit: `75aba4fc824ffe510d321d68dc8d4262fa464692`.
Published implementation: [55f904873846e3dc1879a024e6c7a26041c84d18](https://github.com/alehkot/llm-council/commit/55f904873846e3dc1879a024e6c7a26041c84d18), whose tree exactly matches the reviewed local implementation. Starting main and development were both [2db18d88b696f7efb66e59e4fc3914268be2396b](https://github.com/alehkot/llm-council/commit/2db18d88b696f7efb66e59e4fc3914268be2396b). This documentation-only checkpoint follows that implementation on development; consult its Git history for its own exact SHA.

Independent review identified synchronization and privacy issues that were repaired; its final review found no remaining moderate-or-higher issues within the deliberately unwired experimental scope. At 09:09 UTC, the published implementation had zero GitHub statuses, check runs and Woodpecker commit comments: CI evidence was missing, not proof of a running or passed build. Full build, macOS Keychain and actual macOS storage behavior, live providers and end-to-end consultation execution were unrun. Next, address the documented native durability, ACL and stable-root prerequisites, validate backend acknowledgments, and complete backend execution before connecting this store.

Implementation references: [Rust atomic file creation](https://doc.rust-lang.org/stable/std/fs/struct.OpenOptions.html#method.create_new), [Apple fsync](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html).
