import assert from "node:assert/strict";
import test from "node:test";
import { formatModelName } from "../src/lib/utils.js";

test("model labels keep Pi's provider and model and preserve existing IDs", () => {
	assert.equal(
		formatModelName("pi/openai-codex/gpt-5.6-sol"),
		"openai-codex/gpt-5.6-sol",
	);
	assert.equal(formatModelName("codex/gpt-6-astra"), "gpt-6-astra");
	assert.equal(formatModelName("openai/gpt-5.6-sol"), "gpt-5.6-sol");
	assert.equal(formatModelName("plain-model"), "plain-model");
	assert.equal(formatModelName(undefined), "unknown");
});
