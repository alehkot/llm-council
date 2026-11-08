import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = fileURLToPath(new URL("../..", import.meta.url));
const list = (mode) =>
	spawnSync("bash", [".ci/check-browser.sh", mode, "--list"], {
		cwd: root,
		encoding: "utf8",
	});

test("normal browser builds select only the representative smoke harnesses", () => {
	const result = list("smoke");
	assert.equal(result.status, 0, result.stderr);
	assert.deepEqual(result.stdout.trim().split("\n"), [
		"frontend/tests/auth-probe-recovery.browser.mjs",
		"frontend/tests/auth-replacement.browser.mjs",
		"frontend/tests/composition-submit.browser.mjs",
	]);
});

test("manual full mode retains every browser harness", () => {
	const result = list("full");
	assert.equal(result.status, 0, result.stderr);
	const expected = readdirSync(`${root}/frontend/tests`)
		.filter((name) => name.endsWith(".browser.mjs"))
		.sort()
		.map((name) => `frontend/tests/${name}`);
	assert.deepEqual(result.stdout.trim().split("\n"), expected);
});

test("unknown browser mode fails closed", () => {
	assert.equal(list("ful").status, 64);
});
