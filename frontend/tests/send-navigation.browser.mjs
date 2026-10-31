/**
 * Optional rendered regression test. No provider or real backend is contacted.
 * npm ci; CHROMIUM_EXECUTABLE=/path/to/chrome-headless-shell \
 * PLAYWRIGHT_MODULE=/path/to/playwright-core/index.mjs node tests/send-navigation.browser.mjs
 * Supply your installed Playwright module and sandbox-capable Chromium explicitly.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
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
		"5197",
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
const origin = "http://127.0.0.1:5197";
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
	for (const viewport of [
		{ width: 1280, height: 900 },
		{ width: 390, height: 844 },
	]) {
		for (const activeB of [false, true]) {
			for (const outcome of ["started", "queued", "failure"]) {
				const name = `${viewport.width}px ${outcome} after switching A to ${activeB ? "active" : "idle"} B`;
				const context = await browser.newContext({ viewport });
				const page = await context.newPage();
				page.setDefaultTimeout(10_000);
				const errors = [];
				page.on("pageerror", (error) => errors.push(error.message));
				let sendRoute;
				const sent = new Promise((resolve) => {
					sendRoute = { resolve };
				});
				const fixture = (id) => ({
					id,
					title: `Consultation ${id}`,
					created_at: "2026-01-01T00:00:00Z",
					messages:
						id === "A"
							? []
							: [
									{ role: "user", content: "Existing B question" },
									{
										role: "assistant",
										stage1: null,
										stage2: null,
										stage3: null,
										metadata: {
											status: activeB ? "stage1" : "complete",
											consultation_id: "B-run",
										},
									},
								],
				});
				await context.addInitScript(() =>
					localStorage.setItem("llm_council_api_key", "synthetic-test-key"),
				);
				await page.route("**/*", async (route) => {
					const request = route.request();
					const url = new URL(request.url());
					if (url.origin === origin) return route.continue();
					if (url.origin !== "http://localhost:8001") return route.abort();
					let body;
					if (url.pathname === "/api/conversations/A/message") {
						sendRoute.resolve(route);
						return;
					}
					if (url.pathname === "/api/conversations")
						body = ["A", "B"].map((id) => ({
							...fixture(id),
							message_count: id === "A" ? 0 : 2,
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
						body = {
							cursor: "idle",
							active: activeB
								? [{ conversation_id: "B", status: "stage1" }]
								: [],
						};
					} else if (url.pathname.endsWith("/consultation/status"))
						body = url.pathname.includes("/B/")
							? {
									status: activeB ? "stage1" : "complete",
									consultation_id: "B-run",
									assistant_index: 1,
								}
							: { status: "none" };
					else if (url.pathname.endsWith("/queue"))
						body = {
							queue:
								url.pathname.includes("/B/") && activeB
									? [{ id: "B-queued", content: "Existing B queued question" }]
									: [],
						};
					else if (/\/api\/conversations\/[AB]$/.test(url.pathname))
						body = fixture(url.pathname.at(-1));
					else
						throw new Error(
							`Unexpected synthetic API request: ${request.method()} ${url.pathname}`,
						);
					await route.fulfill({ json: body });
				});
				try {
					await page.goto(`${origin}/?conversation=A`);
					await page
						.locator("#prompt-input")
						.fill("Synthetic delayed A question");
					await page.locator("#prompt-input").press("Enter");
					const pending = await sent;
					if (viewport.width < 1024)
						await page
							.getByRole("button", { name: "Open consultations", exact: true })
							.click();
					await page
						.getByRole("button", {
							name: "Open consultation: Consultation B",
							exact: true,
						})
						.filter({ visible: true })
						.click();
					await page.waitForFunction(async (active) => {
						const { useAppStore } = await import("/src/store/useAppStore.js");
						const s = useAppStore.getState();
						return (
							s.currentConversationId === "B" &&
							(active
								? s.consultationStatus?.consultation_id === "B-run" &&
									s.messageQueue[0]?.id === "B-queued"
								: s.consultationStatus === null &&
									s.isLoading === false &&
									s.messageQueue.length === 0)
						);
					}, activeB);
					const controls = () =>
						page.evaluate(async () => {
							const { useAppStore } = await import("/src/store/useAppStore.js");
							const s = useAppStore.getState();
							return {
								selected: s.currentConversationId,
								loading: s.isLoading,
								status: s.consultationStatus,
								queue: s.messageQueue,
								error: s.error,
							};
						});
					const before = await controls();
					if (outcome === "failure")
						await pending.fulfill({
							status: 500,
							json: { detail: "Synthetic A failure" },
						});
					else
						await pending.fulfill({
							json: { status: outcome, consultation_id: "A-new-run" },
						});
					await page.waitForFunction(async () => {
						const { useAppStore } = await import("/src/store/useAppStore.js");
						const s = useAppStore.getState();
						return (
							s.conversationsById.A.messages.length === 0 ||
							s.conversationsById.A.messages.at(-1)?.metadata
								?.consultation_id === "A-new-run"
						);
					});
					assert.deepEqual(
						await controls(),
						before,
						"Delayed A response must preserve all B controls",
					);
					assert.equal(
						await page.locator(".chat-send-btn-text").textContent(),
						activeB ? "Queue for Council" : "Send to Council",
					);
					assert.equal(
						await page
							.getByText("Synthetic A failure", { exact: true })
							.count(),
						0,
					);
					assert.deepEqual(errors, []);
					results.push(name);
					console.log(`PASS ${name}`);
				} catch (error) {
					failures.push({ name, error: error.message });
					console.error(
						`FAIL ${name}: ${error.message}; page errors: ${errors.join("; ")}`,
					);
				} finally {
					// Closing context cancels any held synthetic response on an early failure.
					await context.close();
				}
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
