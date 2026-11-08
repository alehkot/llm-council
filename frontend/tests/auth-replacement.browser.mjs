/** Rendered auth replacement regressions, using only synthetic API responses.
 * Run against Vite source with CHROMIUM_EXECUTABLE and PLAYWRIGHT_MODULE set.
 * No production build, real credential, or model provider is used.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

const { CHROMIUM_EXECUTABLE, PLAYWRIGHT_MODULE, SCREENSHOT_DIR } = process.env;
assert.ok(CHROMIUM_EXECUTABLE && PLAYWRIGHT_MODULE);
const { chromium } = await import(pathToFileURL(PLAYWRIGHT_MODULE).href);
const root =
	process.env.BROWSER_FRONTEND_ROOT ||
	fileURLToPath(new URL("..", import.meta.url));
const origin = "http://127.0.0.1:5237";
const server = spawn(
	process.execPath,
	[
		"node_modules/vite/bin/vite.js",
		"--host",
		"127.0.0.1",
		"--port",
		"5237",
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
const results = [];
const oldKey = "synthetic-original-key";
const newKey = "synthetic-replacement-key";
const headers = {
	"Access-Control-Allow-Origin": "*",
	"Access-Control-Expose-Headers": "Retry-After",
	"Retry-After": "30",
};
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
	if (SCREENSHOT_DIR) await mkdir(SCREENSHOT_DIR, { recursive: true });
	for (const viewport of [
		{ width: 1280, height: 900 },
		{ width: 390, height: 844 },
	]) {
		for (const scenario of [
			...["probe", "request"].flatMap((phase) =>
				[200, 401, 403, 429].map((status) => ({ phase, status })),
			),
			{ phase: "probe", status: 401, sameKey: true },
			{ phase: "request", status: 401, current: true },
		]) {
			const context = await browser.newContext({ viewport });
			const page = await context.newPage();
			page.setDefaultTimeout(6000);
			let pending;
			let armed = false;
			let posts = 0;
			let currentRejected = false;
			const unexpected = [];
			const pageErrors = [];
			page.on("pageerror", (error) => pageErrors.push(error.message));
			await context.addInitScript(
				(key) => localStorage.setItem("llm_council_api_key", key),
				oldKey,
			);
			await context.route("**/*", async (route) => {
				const request = route.request();
				const url = new URL(request.url());
				if (url.origin === origin) return route.continue();
				if (url.hostname === "fonts.googleapis.com")
					return route.fulfill({ contentType: "text/css", body: "" });
				if (url.origin !== "http://localhost:8001") {
					unexpected.push(url.href);
					return route.abort();
				}
				if (request.method() === "OPTIONS")
					return route.fulfill({
						status: 204,
						headers: {
							...headers,
							"Access-Control-Allow-Headers": "X-API-Key,Content-Type",
							"Access-Control-Allow-Methods": "GET,POST",
						},
					});
				if (request.method() === "POST") posts++;
				if (
					armed &&
					!pending &&
					request.headers()["x-api-key"] === oldKey &&
					url.pathname ===
						(scenario.phase === "probe" ? "/api/config" : "/api/conversations")
				) {
					armed = false;
					pending = route;
					return;
				}
				let body;
				if (url.pathname === "/api/config")
					body = {
						council_models: [],
						chairman_model: "test",
						title_model: "test",
						negative_mode_enabled: false,
					};
				else if (url.pathname === "/api/conversations") body = [];
				else if (url.pathname === "/api/skills") body = { skills: [] };
				else if (url.pathname === "/api/consultations/status") {
					if (url.searchParams.has("cursor")) return;
					body = { active: [], cursor: "idle" };
				} else {
					unexpected.push(`${request.method()} ${url.pathname}`);
					return route.abort();
				}
				return route.fulfill({ headers, json: body });
			});
			try {
				// Vite's first source transform exceeds the 6s action budget on
				// the CPU-capped Pi worker. Keep that budget for auth assertions,
				// but allow bounded cold-start navigation before checking the UI.
				await page.goto(origin, { timeout: 30000 });
				await page
					.getByRole("button", { name: "API key status", exact: true })
					.waitFor();
				await page.evaluate(async () => {
					window.testApi = (await import("/src/api.js")).api;
					window.testStore = (
						await import("/src/store/useAppStore.js")
					).useAppStore;
					window.authEvents = [];
					window.addEventListener("llm-council:api-auth-error", (event) =>
						window.authEvents.push(event.detail.message),
					);
				});
				await page.waitForFunction(
					() => window.testStore.getState().config !== null,
				);
				armed = true;
				await page.evaluate((phase) => {
					if (phase === "probe") window.testApi.resetAuthState();
					window.oldResult = (
						phase === "probe"
							? window.testApi.createConversation()
							: window.testApi.listConversations()
					).then(
						() => "fulfilled",
						(error) => error.name,
					);
				}, scenario.phase);
				for (let i = 0; i < 60 && !pending; i++) await delay(50);
				assert.ok(pending, "old request must be held before replacement");
				if (!scenario.current) {
					// Exercise the real clear/save handlers twice, not just the API seam.
					for (const key of [
						"synthetic-intermediate-key",
						scenario.sameKey ? oldKey : newKey,
					]) {
						await page
							.getByRole("button", { name: "API key status", exact: true })
							.click();
						await page
							.getByRole("menuitem", { name: "Clear API Key", exact: true })
							.click();
						await page.getByRole("dialog").waitFor();
						assert.equal(
							await page.evaluate(() =>
								localStorage.getItem("llm_council_api_key"),
							),
							null,
						);
						await page.locator("#apiKey").fill(key);
						// The Pi also stalled at this click in the full CI run.
						// Keep other actions/assertions at 6s and the suite at 240s.
						await page
							.getByRole("button", { name: "Save API Key", exact: true })
							.click({ timeout: 15000 });
						await page.getByRole("dialog").waitFor({ state: "hidden" });
						await page.waitForFunction(
							() => window.testStore.getState().config !== null,
						);
					}
				}
				await pending.fulfill({ status: scenario.status, headers, json: [] });
				const result = await page.evaluate(() => window.oldResult);
				if (scenario.current) {
					await page.getByRole("dialog").waitFor();
					assert.equal(
						await page.evaluate(() =>
							localStorage.getItem("llm_council_api_key"),
						),
						null,
					);
					assert.equal(
						await page
							.getByText(
								"Your API key was invalid or has expired. Please enter a new one.",
							)
							.count(),
						1,
					);
					currentRejected = true;
				} else {
					assert.equal(result, "AbortError");
					assert.equal(
						await page.evaluate(() =>
							localStorage.getItem("llm_council_api_key"),
						),
						scenario.sameKey ? oldKey : newKey,
					);
					assert.equal(await page.getByRole("dialog").count(), 0);
					assert.deepEqual(await page.evaluate(() => window.authEvents), []);
					assert.equal(
						await page.evaluate(() => window.testStore.getState().error),
						null,
					);
					assert.equal(
						await page.evaluate(
							async () => (await window.testApi.listConversations()).length,
						),
						0,
					);
				}
				assert.equal(
					posts,
					0,
					"old queued create must never send under replacement credentials",
				);
				assert.deepEqual(unexpected, []);
				assert.deepEqual(pageErrors, []);
				if (
					SCREENSHOT_DIR &&
					scenario.status === 401 &&
					scenario.phase === "request"
				)
					await page.screenshot({
						path: join(
							SCREENSHOT_DIR,
							`auth-${viewport.width}-${scenario.current ? "current-rejected" : "replacement-preserved"}.png`,
						),
						fullPage: true,
						animations: "disabled",
					});
				results.push({
					viewport: viewport.width,
					...scenario,
					currentRejected,
					passed: true,
				});
			} finally {
				await context.close();
			}
		}
	}
	console.log(
		JSON.stringify(
			{
				passed: results.length,
				results,
				limitations:
					"Synthetic API; no live provider or real credential. Development-server rendered Chromium desktop/mobile checks only; external fonts replaced with empty CSS.",
			},
			null,
			2,
		),
	);
} finally {
	await browser?.close();
	if (server.exitCode === null) {
		server.kill("SIGTERM");
		await once(server, "exit");
	}
}
