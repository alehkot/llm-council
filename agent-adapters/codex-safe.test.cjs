const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(`${__dirname}/codex-safe.cjs`, "utf8");

function run(probe) {
	const calls = [];
	const errors = [];
	let exitCode;
	const childProcess = {
		spawnSync(executable, args, options) {
			calls.push({ executable, args, options, probe: true });
			return probe;
		},
		spawn(executable, args, options) {
			calls.push({ executable, args, options });
			return { on() {} };
		},
	};
	const requireStub = () => childProcess;
	requireStub.resolve = () => "/pinned/codex.js";
	vm.runInNewContext(source, {
		require: requireStub,
		process: {
			execPath: "/node",
			argv: ["node", "launcher", "app-server"],
			stderr: { write: (text) => errors.push(text) },
			exit: (code) => {
				exitCode = code;
			},
		},
	});
	return { calls, errors, exitCode };
}

test("disables every inherited MCP server with quoted keys before starting Codex", () => {
	const names = ["ordinary", "contains.dots", 'contains"quote'];
	const result = run({
		status: 0,
		stdout: JSON.stringify(names.map((name) => ({ name }))),
	});
	assert.equal(result.calls.length, 2);
	assert.ok(result.calls[0].args.includes("--json"));
	const override = result.calls[1].args.find((arg) =>
		arg.startsWith("mcp_servers="),
	);
	for (const name of names) {
		assert.ok(override.includes(`${JSON.stringify(name)}={enabled=false}`));
	}
	assert.ok(result.calls[1].args.includes('forced_login_method="chatgpt"'));
	assert.ok(result.calls[1].args.includes("features.hooks=false"));
	assert.ok(result.calls[1].args.includes("project_doc_max_bytes=0"));
});

test("refuses unverified configuration without leaking native output", () => {
	for (const probe of [
		{ status: 1, stdout: "secret", stderr: "secret" },
		{ status: 0, stdout: "not JSON: secret" },
		{ status: 0, stdout: "{}" },
		{ status: 0, stdout: '[{"name":null}]' },
	]) {
		const result = run(probe);
		assert.equal(result.calls.length, 1);
		assert.equal(result.exitCode, 1);
		assert.equal(
			result.errors.join(""),
			"Cannot prepare restricted Codex configuration.\n",
		);
	}
});
