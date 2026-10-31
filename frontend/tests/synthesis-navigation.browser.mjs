/**
 * Optional rendered regression test. No provider or real backend is contacted.
 * npm ci; CHROMIUM_EXECUTABLE=/path/to/chrome-headless-shell \
 * PLAYWRIGHT_MODULE=/path/to/playwright-core/index.mjs node tests/synthesis-navigation.browser.mjs
 * Supply your installed Playwright module and sandbox-capable Chromium explicitly.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { once } from "node:events";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const { CHROMIUM_EXECUTABLE, PLAYWRIGHT_MODULE } = process.env;
assert.ok(
	CHROMIUM_EXECUTABLE && PLAYWRIGHT_MODULE,
	"Set CHROMIUM_EXECUTABLE and PLAYWRIGHT_MODULE",
);
const { chromium } = await import(pathToFileURL(PLAYWRIGHT_MODULE).href);
const root =
	process.env.BROWSER_FRONTEND_ROOT ||
	fileURLToPath(new URL("..", import.meta.url));
const server = spawn(
	process.execPath,
	[
		"node_modules/vite/bin/vite.js",
		"--host",
		"127.0.0.1",
		"--port",
		"5215",
		"--strictPort",
	],
	{
		cwd: root,
		stdio: ["ignore", "pipe", "pipe"],
	},
);
let serverLog = "";
server.stdout.on("data", (data) => {
	serverLog += data;
});
server.stderr.on("data", (data) => {
	serverLog += data;
});
const origin = "http://127.0.0.1:5215";
let browser;
const failures = [];
const results = [];
let deadline;
const timeout = new Promise((_, reject) => {
	deadline = setTimeout(
		() => reject(new Error("Browser test deadline exceeded")),
		120_000,
	);
});
const run = async () => {
	let ready = false;
	for (let i = 0; i < 100; i++) {
		if (server.exitCode !== null) throw new Error(serverLog);
		try {
			ready = (await fetch(origin)).ok;
		} catch {
			/* startup */
		}
		if (ready) break;
		await delay(100);
	}
	assert.ok(ready, `Vite startup failed: ${serverLog}`);
	browser = await chromium.launch({
		executablePath: CHROMIUM_EXECUTABLE,
		headless: true,
		chromiumSandbox: true,
	});
	if (process.env.SCREENSHOT_DIR)
		await mkdir(process.env.SCREENSHOT_DIR, { recursive: true });
	for (const viewport of [
		{ width: 1280, height: 900 },
		{ width: 390, height: 844 },
	]) {
		for (const scenario of [
			"navigate-success",
			"navigate-failure",
			"all-failure",
			"same-success",
			"same-failure",
			"revisit-success",
			"revisit-failure",
		]) {
			const name = `${viewport.width}px synthesis ${scenario}`;
			const context = await browser.newContext({ viewport });
			const page = await context.newPage();
			page.setDefaultTimeout(8000);
			const errors = [];
			page.on("pageerror", (error) => errors.push(error.message));
			let resolveFailure;
			const failureHandled = new Promise((resolve) => {
				resolveFailure = resolve;
			});
			page.on("console", (message) => {
				if (/^(Resynthesis failed:|Generate all failed:)/.test(message.text()))
					resolveFailure();
			});
			let resolveHeld;
			const held = new Promise((resolve) => {
				resolveHeld = resolve;
			});
			const version = (id, index) => ({
				model: "test/model",
				response: `${id} synthesis ${index}`,
				mode: index ? "consensus" : "best_of_breed",
				timestamp: `2026-01-0${index + 1}T00:00:00Z`,
			});
			const fixture = (id) => {
				const versions = [
					version(id, 0),
					...(id === "B" ? [version(id, 1), version(id, 2)] : []),
				];
				return {
					id,
					title: `Consultation ${id}`,
					created_at: "2026-01-01T00:00:00Z",
					messages: [
						{ role: "user", content: `Question ${id}` },
						{
							role: "assistant",
							stage1: [{ model: "test/model", response: `Answer ${id}` }],
							stage2: [],
							stage3: { current: versions[0], versions },
							metadata: { status: "complete" },
						},
					],
				};
			};
			const conversations = { A: fixture("A"), B: fixture("B") };
			const requests = [];
			await context.addInitScript(() =>
				localStorage.setItem("llm_council_api_key", "synthetic-test-key"),
			);
			await page.route("**/*", async (route) => {
				const request = route.request();
				const url = new URL(request.url());
				if (url.origin === origin) return route.continue();
				if (url.origin !== "http://localhost:8001") return route.abort();
				let body;
				if (/\/resynthesize(?:-all)?$/.test(url.pathname)) {
					requests.push(url.pathname);
					resolveHeld(route);
					return;
				}
				if (url.pathname === "/api/conversations")
					body = Object.values(conversations).map((c) => ({
						...c,
						message_count: 2,
					}));
				else if (url.pathname === "/api/config")
					body = {
						negative_mode_enabled: false,
						council_models: [],
						chairman_model: "test",
						title_model: "test",
					};
				else if (url.pathname === "/api/skills") body = { skills: [] };
				else if (url.pathname === "/api/consultations/status") {
					if (url.searchParams.has("cursor")) return;
					body = { active: [], cursor: "idle" };
				} else if (url.pathname.endsWith("/consultation/status"))
					body = { status: "none" };
				else if (url.pathname.endsWith("/queue")) body = { queue: [] };
				else if (/\/api\/conversations\/[AB]$/.test(url.pathname))
					body = conversations[url.pathname.at(-1)];
				else
					throw new Error(
						`Unexpected synthetic API request: ${request.method()} ${url.pathname}`,
					);
				await route.fulfill({ json: body });
			});
			const selectConversation = async (id) => {
				if (viewport.width < 1024)
					await page
						.getByRole("button", { name: "Open consultations", exact: true })
						.click();
				await page
					.getByRole("button", {
						name: `Open consultation: Consultation ${id}`,
						exact: true,
					})
					.filter({ visible: true })
					.click();
				await page.getByText(`${id} synthesis 0`, { exact: true }).waitFor();
			};
			const addButton = () =>
				page.getByRole("button", { name: "Add Synthesis", exact: true });
			try {
				// Cache B first. Navigation to an uncached row can hide the reuse bug by unmounting the view.
				await page.goto(`${origin}/?conversation=B`);
				await page.getByText("B synthesis 0", { exact: true }).waitFor();
				await selectConversation("A");
				const generateAll = scenario === "all-failure";
				await page
					.getByRole("button", {
						name: generateAll ? "Generate All" : "Add Synthesis",
						exact: true,
					})
					.click();
				const pending = await held;
				assert.deepEqual(requests, [
					`/api/conversations/A/resynthesize${generateAll ? "-all" : ""}`,
				]);
				const revisit = scenario.startsWith("revisit-");
				const navigate = !scenario.startsWith("same-") && !revisit;
				if (navigate || revisit) {
					await selectConversation("B");
					assert.equal(
						await addButton().count(),
						1,
						"A pending request must not replace B Add Synthesis control",
					);
					assert.equal(
						await addButton().isEnabled(),
						true,
						"A pending request must not disable B synthesis",
					);
					assert.equal(
						await page
							.getByRole("button", { name: "Generate All", exact: true })
							.isEnabled(),
						true,
					);
					await page.locator(".stage3").getByRole("tab").nth(2).click();
					await page.getByText("B synthesis 2", { exact: true }).waitFor();
				}
				if (revisit) await selectConversation("A");
				if (!navigate) {
					const busy = page
						.locator(".stage3 button")
						.filter({ hasText: /Adding/ });
					assert.equal(
						await busy.isDisabled(),
						true,
						"Same/revisited conversation pending request remains disabled",
					);
					assert.equal(
						await page
							.getByRole("button", { name: "Generate All", exact: true })
							.isEnabled(),
						false,
					);
				}
				const succeeds = scenario.endsWith("success");
				if (succeeds) {
					const next = version("A", 1);
					conversations.A.messages[1].stage3.versions.push(next);
					conversations.A.messages[1].stage3.current = next;
					await pending.fulfill({
						json: { status: "ok", version: next, version_index: 1 },
					});
					await page.waitForFunction(async () => {
						const { useAppStore } = await import("/src/store/useAppStore.js");
						return (
							useAppStore.getState().conversationsById.A?.messages[1].stage3
								.versions.length === 2
						);
					});
				} else {
					await pending.fulfill({
						status: 500,
						json: { detail: "Synthetic A synthesis failure" },
					});
					// A's handler logs even after its component unmounts. A spinner on B cannot prove completion.
					await failureHandled;
					await page.evaluate(
						() =>
							new Promise((resolve) =>
								requestAnimationFrame(() => requestAnimationFrame(resolve)),
							),
					);
				}
				const state = await page.evaluate(async () => {
					const { useAppStore } = await import("/src/store/useAppStore.js");
					const s = useAppStore.getState();
					return {
						selected: s.currentConversationId,
						A: s.conversationsById.A.messages[1].stage3,
						B: s.conversationsById.B.messages[1].stage3,
					};
				});
				assert.equal(state.selected, navigate ? "B" : "A");
				assert.equal(state.A.versions.length, succeeds ? 2 : 1);
				assert.deepEqual(
					state.B,
					conversations.B.messages[1].stage3,
					"A completion must not change B data",
				);
				if (navigate) {
					assert.equal(
						await page
							.getByText("Synthetic A synthesis failure", { exact: true })
							.count(),
						0,
						"A error must not leak into B",
					);
					await page.getByText("B synthesis 2", { exact: true }).waitFor();
					assert.equal(
						await page
							.locator(".stage3")
							.getByRole("tab")
							.nth(2)
							.getAttribute("aria-selected"),
						"true",
					);
				} else if (succeeds) {
					await page.getByText("A synthesis 1", { exact: true }).waitFor();
				} else {
					await page
						.getByText("Synthetic A synthesis failure", { exact: true })
						.waitFor();
				}
				assert.equal(
					await addButton().isEnabled(),
					true,
					"Controls must be available after completion or failure",
				);
				assert.deepEqual(errors, []);
				if (process.env.SCREENSHOT_DIR && scenario === "navigate-failure") {
					await page.screenshot({
						path: join(
							process.env.SCREENSHOT_DIR,
							`synthesis-${viewport.width}.png`,
						),
						fullPage: true,
						animations: "disabled",
					});
				}
				results.push(name);
				console.log(`PASS ${name}`);
			} catch (error) {
				failures.push({ name, error: error.message });
				console.error(
					`FAIL ${name}: ${error.message}; page errors: ${errors.join("; ")}`,
				);
			} finally {
				await context.close();
			}
		}
	}
};
try {
	await Promise.race([run(), timeout]);
} finally {
	clearTimeout(deadline);
	await browser?.close();
	if (server.exitCode === null) {
		const exited = once(server, "exit");
		server.kill("SIGTERM");
		const forceStop = setTimeout(() => server.kill("SIGKILL"), 3000);
		try {
			await exited;
		} finally {
			clearTimeout(forceStop);
		}
	}
}
console.log(
	JSON.stringify(
		{ passed: results.length, failed: failures.length, failures },
		null,
		2,
	),
);
if (failures.length) process.exitCode = 1;
