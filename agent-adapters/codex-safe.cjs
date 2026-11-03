#!/usr/bin/env node
// Codex merges MCP maps, so disabling each inherited server is required.
const { spawn, spawnSync } = require("node:child_process");
const codex = require.resolve("@openai/codex/bin/codex.js");
const settings = [
	'model_provider="openai"',
	'forced_login_method="chatgpt"',
	'web_search="disabled"',
	'sandbox_mode="read-only"',
	'approval_policy="never"',
	"project_doc_max_bytes=0",
	"features.skip_host_skill_discovery=true",
	...[
		"hooks",
		"plugins",
		"apps",
		"shell_tool",
		"unified_exec",
		"multi_agent",
		"browser_use",
		"computer_use",
		"image_generation",
		"view_image",
	].map((feature) => `features.${feature}=false`),
].flatMap((value) => ["-c", value]);

try {
	const probe = spawnSync(
		process.execPath,
		[codex, "mcp", "list", "--json", ...settings],
		{
			encoding: "utf8",
			timeout: 10000,
			maxBuffer: 10_000_000,
			stdio: ["ignore", "pipe", "pipe"],
		},
	);
	if (probe.error || probe.status !== 0)
		throw new Error("MCP configuration unavailable");
	const servers = JSON.parse(probe.stdout);
	if (!Array.isArray(servers)) throw new Error("Invalid MCP configuration");
	const disabled = servers.map((server) => {
		if (typeof server.name !== "string" || !server.name)
			throw new Error("Invalid MCP name");
		return `${JSON.stringify(server.name)}={enabled=false}`;
	});
	settings.push("-c", `mcp_servers={${disabled.join(",")}}`);
	const child = spawn(
		process.execPath,
		[codex, ...process.argv.slice(2), ...settings],
		{
			stdio: "inherit",
		},
	);
	child.on("error", () => process.exit(1));
	child.on("exit", (code) => process.exit(code ?? 1));
} catch {
	// Native configuration may contain credentials; never echo probe output.
	process.stderr.write("Cannot prepare restricted Codex configuration.\n");
	process.exit(1);
}
