/**
 * Rendered IME Enter-key regression. Synthetic APIs only; no provider calls.
 * Supply installed PLAYWRIGHT_MODULE and sandbox-capable CHROMIUM_EXECUTABLE.
 * Optional BROWSER_FRONTEND_ROOT checks an unchanged baseline; SCREENSHOT_DIR
 * retains desktop/mobile evidence. The script manages its own Vite server.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const { CHROMIUM_EXECUTABLE, PLAYWRIGHT_MODULE, SCREENSHOT_DIR } = process.env;
assert.ok(
	CHROMIUM_EXECUTABLE && PLAYWRIGHT_MODULE,
	"Set CHROMIUM_EXECUTABLE and PLAYWRIGHT_MODULE",
);
const { chromium } = await import(pathToFileURL(PLAYWRIGHT_MODULE).href);
const root =
	process.env.BROWSER_FRONTEND_ROOT ||
	fileURLToPath(new URL("..", import.meta.url));
const origin = "http://127.0.0.1:5218";
const server = spawn(
	process.execPath,
	[
		"node_modules/vite/bin/vite.js",
		"--host",
		"127.0.0.1",
		"--port",
		"5218",
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
let browser;
let deadline;
const results = [];
const failures = [];
const timeout = new Promise((_, reject) => {
	deadline = setTimeout(
		() => reject(new Error("Browser test deadline exceeded")),
		120_000,
	);
});
const original = {
	input: "  Preserve this carefully written question  ",
	negativePrompt: "  Check assumptions and risks  ",
};

async function run() {
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
	if (SCREENSHOT_DIR) await mkdir(SCREENSHOT_DIR, { recursive: true });
	for (const viewport of [
		{ width: 1280, height: 900 },
		{ width: 390, height: 844 },
	]) {
		for (const scenario of [
			"prompt-composition",
			"negative-composition",
			"prompt-process-key",
			"negative-process-key",
			"plain-enter",
			"shift-enter",
		]) {
			const name = `${viewport.width}px draft ${scenario}`;
			const context = await browser.newContext({ viewport });
			const page = await context.newPage();
			page.setDefaultTimeout(5000);
			const errors = [];
			page.on("pageerror", (error) => errors.push(error.message));
			const sends = [];
			const held = [];
			const fixture = (id) => ({
				id,
				title: `Consultation ${id}`,
				created_at: "2026-01-01T00:00:00Z",
				messages:
					id === "A"
						? []
						: [
								{ role: "user", content: "Saved B question" },
								{
									role: "assistant",
									stage1: null,
									stage2: null,
									stage3: null,
									metadata: { status: "complete" },
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
				if (url.pathname.endsWith("/message")) {
					sends.push({ path: url.pathname, body: request.postDataJSON() });
					held.push(route);
					return;
				}
				let body;
				if (url.pathname === "/api/conversations")
					body = ["A", "B"].map((id) => ({
						...fixture(id),
						message_count: id === "A" ? 0 : 2,
					}));
				else if (url.pathname === "/api/config")
					body = {
						negative_mode_enabled: true,
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
					body = fixture(url.pathname.at(-1));
				else
					throw new Error(
						`Unexpected synthetic request: ${request.method()} ${url.pathname}`,
					);
				await route.fulfill({ json: body });
			});
			const fields = async () => ({
				input: await page.locator("#prompt-input").inputValue(),
				negativePrompt: await page
					.locator("#negative-prompt-input")
					.inputValue(),
			});
			const fill = async (draft) => {
				await page.locator("#prompt-input").fill(draft.input);
				await page.locator("#negative-prompt-input").fill(draft.negativePrompt);
			};
			const nextSend = async (index) => {
				for (let i = 0; i < 100 && !held[index]; i++) await delay(20);
				assert.ok(held[index], "Explicit submission reached the synthetic API");
				assert.equal(sends[index].path, "/api/conversations/A/message");
				assert.deepEqual(sends[index].body, {
					content: original.input,
					negative_prompt: original.negativePrompt,
				});
				return held[index];
			};
			try {
				await page.goto(`${origin}/?conversation=A`);
				await page.locator("#prompt-input").waitFor();
				await fill(original);
				const target = page.locator(
					scenario.startsWith("negative")
						? "#negative-prompt-input"
						: "#prompt-input",
				);
				if (scenario === "plain-enter") {
					await target.press("Enter");
					await nextSend(0);
					assert.equal(sends.length, 1);
				} else if (scenario === "shift-enter") {
					await target.press("Shift+Enter");
					await delay(150);
					assert.equal(sends.length, 0, "Shift+Enter must not submit");
					assert.ok((await target.inputValue()).includes("\n"));
				} else {
					const composing = scenario.endsWith("composition");
					const prevented = await target.evaluate((el, composing) => {
						const event = new KeyboardEvent("keydown", {
							key: "Enter",
							code: "Enter",
							bubbles: true,
							cancelable: true,
							isComposing: composing,
							keyCode: composing ? 13 : 229,
						});
						el.dispatchEvent(event);
						return event.defaultPrevented;
					}, composing);
					await delay(150);
					assert.equal(
						sends.length,
						0,
						"IME confirmation must not submit an unfinished draft",
					);
					assert.equal(
						prevented,
						false,
						"IME confirmation must keep its default action",
					);
					assert.deepEqual(
						await fields(),
						original,
						"IME confirmation preserves both drafts",
					);
					await target.press("Enter");
					await nextSend(0);
					assert.equal(
						sends.length,
						1,
						"Enter after composition still submits once",
					);
				}
				assert.deepEqual(errors, []);
				if (SCREENSHOT_DIR)
					await page.screenshot({
						path: join(
							SCREENSHOT_DIR,
							`composition-${viewport.width}-${scenario}.png`,
						),
						fullPage: true,
					});
				results.push(name);
				console.log(`PASS ${name}`);
			} catch (error) {
				failures.push({ name, error: error.message });
				console.error(`FAIL ${name}: ${error.message}`);
				if (SCREENSHOT_DIR)
					await page.screenshot({
						path: join(
							SCREENSHOT_DIR,
							`composition-${viewport.width}-${scenario}-failure.png`,
						),
						fullPage: true,
					});
			} finally {
				await context.close();
			}
		}
	}
}
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
