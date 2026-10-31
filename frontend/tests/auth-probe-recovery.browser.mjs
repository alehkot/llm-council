/** Real browser transport recovery with synthetic responses, no live credentials. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const { CHROMIUM_EXECUTABLE, PLAYWRIGHT_MODULE } = process.env;
assert.ok(CHROMIUM_EXECUTABLE && PLAYWRIGHT_MODULE);
const { chromium } = await import(pathToFileURL(PLAYWRIGHT_MODULE).href);
const root =
	process.env.BROWSER_FRONTEND_ROOT ||
	fileURLToPath(new URL("..", import.meta.url));
const origin = "http://127.0.0.1:5231";
const server = spawn(
	process.execPath,
	[
		"node_modules/vite/bin/vite.js",
		"--host",
		"127.0.0.1",
		"--port",
		"5231",
		"--strictPort",
	],
	{ cwd: root, stdio: ["ignore", "pipe", "pipe"] },
);
let output = "";
server.stdout.on("data", (data) => {
	output += data;
});
server.stderr.on("data", (data) => {
	output += data;
});
let browser;
let passed = 0;
try {
	let ready = false;
	for (let i = 0; i < 100; i++) {
		if (server.exitCode !== null) throw new Error(output);
		try {
			ready = (await fetch(origin)).ok;
		} catch {
			/* starting */
		}
		if (ready) break;
		await delay(100);
	}
	assert.ok(ready, output);
	browser = await chromium.launch({
		executablePath: CHROMIUM_EXECUTABLE,
		headless: true,
		chromiumSandbox: true,
	});
	for (const viewport of [
		{ width: 1280, height: 900 },
		{ width: 390, height: 844 },
	]) {
		for (const scenario of ["network", "unauthorized", "rate-limited"]) {
			const context = await browser.newContext({ viewport });
			try {
				const page = await context.newPage();
				let probes = 0;
				let requests = 0;
				const unexpected = [];
				await context.route("**/*", async (route) => {
					const url = new URL(route.request().url());
					if (url.pathname.startsWith("/api/")) {
						if (url.pathname === "/api/config") {
							probes++;
							if (probes === 1) {
								if (scenario === "network") return route.abort("failed");
								return route.fulfill({
									status: scenario === "unauthorized" ? 401 : 429,
									headers: {
										"Access-Control-Allow-Origin": "*",
										"Retry-After": "30",
									},
									body: "{}",
								});
							}
						} else {
							requests++;
						}
						return route.fulfill({
							status: 200,
							contentType: "application/json",
							headers: { "Access-Control-Allow-Origin": "*" },
							body: "[]",
						});
					}
					if (url.origin !== origin) {
						unexpected.push(url.href);
						return route.abort();
					}
					if (url.pathname === "/")
						return route.fulfill({
							contentType: "text/html",
							body: "<!doctype html><title>API transport fixture</title>",
						});
					return route.continue();
				});
				await page.goto(origin);
				const results = await page.evaluate(async () => {
					localStorage.setItem("llm_council_api_key", "synthetic-test-key");
					const { api } = await import("/src/api.js");
					const first = await Promise.allSettled([
						api.listConversations(),
						api.listConversations(),
					]);
					const second = await Promise.allSettled([
						api.listConversations(),
						api.listConversations(),
					]);
					return [first, second].map((batch) => batch.map((r) => r.status));
				});
				assert.deepEqual(results[0], ["rejected", "rejected"]);
				assert.deepEqual(
					results[1],
					scenario === "network"
						? ["fulfilled", "fulfilled"]
						: ["rejected", "rejected"],
				);
				assert.equal(probes, scenario === "network" ? 2 : 1);
				assert.equal(requests, scenario === "network" ? 2 : 0);
				assert.deepEqual(unexpected, []);
				passed++;
			} finally {
				await context.close();
			}
		}
	}
	console.log(
		JSON.stringify({
			passed,
			limitations:
				"Real browser API-module transport, not a rendered UI or live-provider evaluation.",
		}),
	);
} finally {
	await browser?.close();
	if (server.exitCode === null) {
		server.kill("SIGTERM");
		await once(server, "exit");
	}
}
