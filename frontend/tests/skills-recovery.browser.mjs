/**
 * Rendered skill-catalogue recovery regressions against Vite source.
 * Synthetic API only; no production build, real credentials, or providers.
 * CHROMIUM_EXECUTABLE=/path/to/chrome-headless-shell \
 * PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tests/skills-recovery.browser.mjs
 * Requires an installed sandbox-capable Chromium. Never disable its sandbox.
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
const port = process.env.BROWSER_PORT || "5243";
const origin = `http://127.0.0.1:${port}`;
const server = spawn(
	process.execPath,
	[
		"node_modules/vite/bin/vite.js",
		"--host",
		"127.0.0.1",
		"--port",
		port,
		"--strictPort",
	],
	{ cwd: root, stdio: ["ignore", "pipe", "pipe"] },
);
let serverLog = "";
server.stdout.on("data", (data) => {
	serverLog += data;
});
server.stderr.on("data", (data) => {
	serverLog += data;
});
let browser;
let stopped = false;
let deadline;
const failures = [];
const results = [];
const headers = {
	"Access-Control-Allow-Origin": "*",
	"Access-Control-Allow-Headers": "X-API-Key,Content-Type",
	"Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
};
const skill = {
	id: "review",
	name: "Review",
	description: "Synthetic review skill",
	enabled: true,
	version: "1",
	counts: {},
};
const conversation = {
	id: "A",
	title: "Synthetic consultation",
	created_at: "2026-01-01T00:00:00Z",
	messages: [],
};

async function run() {
	let ready = false;
	for (let i = 0; i < 100 && !stopped; i++) {
		if (server.exitCode !== null) throw new Error(serverLog);
		try {
			ready = (await fetch(origin)).ok;
		} catch {
			/* startup */
		}
		if (ready) break;
		await delay(100);
	}
	assert.ok(ready, serverLog);
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
			"initial503",
			"initialPending",
			"refresh503",
			"empty",
			"disabled",
			"featureDisabled",
		]) {
			if (stopped) return;
			const name = `${viewport.width}px ${scenario}`;
			const context = await browser.newContext({ viewport });
			const page = await context.newPage();
			page.setDefaultTimeout(6000);
			let catalogue =
				scenario === "initial503"
					? "503"
					: scenario === "initialPending"
						? "pending"
						: "good";
			let featureEnabled = true;
			let skillRequests = 0;
			let pendingSkills;
			let sources = { codex: false, claude: false };
			const posts = [];
			const unexpected = [];
			const errors = [];
			page.on("pageerror", (error) => errors.push(error.message));
			await context.addInitScript(() => {
				localStorage.setItem("llm_council_api_key", "synthetic-test-key");
				if (localStorage.getItem("llm_council_skills_default") === null) {
					localStorage.setItem("llm_council_skills_default", '["review"]');
				}
				// Do not request/grant browser notification permission during tests.
				Object.defineProperty(window, "Notification", {
					value: undefined,
					configurable: true,
				});
			});
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
					return route.fulfill({ status: 204, headers });
				let body;
				if (url.pathname === "/api/config")
					body = {
						skills_enabled: featureEnabled,
						skills_max_selected: 5,
						negative_mode_enabled: false,
						council_models: ["test/model"],
						chairman_model: "test/model",
						title_model: "test/model",
					};
				else if (url.pathname === "/api/skills" && request.method() === "GET") {
					skillRequests++;
					if (!featureEnabled)
						return route.fulfill({
							status: 404,
							headers,
							json: { detail: "Not Found" },
						});
					if (catalogue === "pending") {
						pendingSkills = route;
						return;
					}
					if (catalogue === "503")
						return route.fulfill({
							status: 503,
							headers,
							json: { detail: "Synthetic catalogue outage" },
						});
					body = {
						skills:
							catalogue === "empty"
								? []
								: [{ ...skill, enabled: catalogue !== "disabled" }],
						sources,
					};
				} else if (
					url.pathname === "/api/skills/sources" &&
					request.method() === "PUT"
				) {
					sources = request.postDataJSON();
					body = { sources };
				} else if (url.pathname === "/api/skills/review") body = skill;
				else if (url.pathname === "/api/skills/review/markdown")
					body = { markdown: "# Review\nSynthetic skill only." };
				else if (url.pathname === "/api/conversations")
					body = [{ ...conversation, message_count: 0 }];
				else if (url.pathname === "/api/conversations/A") body = conversation;
				else if (
					url.pathname === "/api/conversations/A/message" &&
					request.method() === "POST"
				) {
					posts.push(request.postDataJSON());
					if (!featureEnabled && posts.at(-1).skill_ids?.length)
						return route.fulfill({
							status: 422,
							headers,
							json: { detail: "Skills disabled" },
						});
					body = { status: "started", consultation_id: "synthetic-run" };
				} else if (url.pathname === "/api/consultations/status") {
					if (url.searchParams.has("cursor")) return;
					body = { active: [], cursor: "idle" };
				} else if (url.pathname === "/api/conversations/A/consultation/status")
					body = { status: "none" };
				else {
					unexpected.push(`${request.method()} ${url.pathname}`);
					return route.abort();
				}
				return route.fulfill({ headers, json: body });
			});
			const snapshot = () =>
				page.evaluate(async () => {
					const { useAppStore } = await import("/src/store/useAppStore.js");
					const state = useAppStore.getState();
					return {
						stored: JSON.parse(
							localStorage.getItem("llm_council_skills_default"),
						),
						selected: state.selectedSkillIds,
						loaded: state.skillsLoaded,
						skills: state.skills.map((item) => item.id),
						messages: state.conversationsById.A?.messages.length,
						error: state.error,
					};
				});
			const settled = async () => {
				await page.waitForFunction(async () => {
					const { useAppStore } = await import("/src/store/useAppStore.js");
					const state = useAppStore.getState();
					return state.config && state.conversationsById.A;
				});
				await page.locator("#prompt-input").waitFor();
			};
			const send = async () => {
				await page.locator("#prompt-input").fill("Keep my review skill");
				await page
					.getByRole("button", { name: "Send to Council", exact: true })
					.click();
			};
			try {
				await page.goto(`${origin}/?conversation=A`);
				await settled();
				if (scenario.startsWith("initial")) {
					if (scenario === "initial503")
						await page.waitForFunction(async () => {
							const { useAppStore } = await import("/src/store/useAppStore.js");
							const state = useAppStore.getState();
							return state.error || state.skillsLoaded;
						});
					else {
						for (let i = 0; i < 60 && !pendingSkills; i++) await delay(50);
						assert.ok(pendingSkills, "Catalogue request is held");
					}
					await send();
					await page.waitForFunction(async () => {
						const { useAppStore } = await import("/src/store/useAppStore.js");
						return (
							useAppStore.getState().error ||
							document.querySelector("#prompt-input").value === ""
						);
					});
					assert.equal(posts.length, 0, "No send while catalogue unavailable");
					assert.equal(
						await page.locator("#prompt-input").inputValue(),
						"Keep my review skill",
						"Draft retained",
					);
					let state = await snapshot();
					assert.deepEqual(state.stored, ["review"], "Saved default preserved");
					assert.equal(state.messages, 0, "No optimistic message appended");
					assert.equal(
						state.loaded,
						false,
						"Unavailable catalogue is not authoritative",
					);
					assert.ok(state.error, "Actionable failure shown");
					catalogue = "good";
					if (pendingSkills) {
						await pendingSkills.fulfill({
							headers,
							json: { skills: [skill], sources },
						});
						pendingSkills = null;
					} else await page.reload();
					await settled();
					await page.waitForFunction(async () =>
						(await import("/src/store/useAppStore.js")).useAppStore
							.getState()
							.selectedSkillIds?.includes("review"),
					);
					await page
						.getByRole("button", { name: "Manage agent skills", exact: true })
						.click();
					assert.equal(
						await page
							.locator("#skill-default-review")
							.getAttribute("aria-checked"),
						"true",
					);
					await page.keyboard.press("Escape");
					await send();
					for (let i = 0; i < 60 && posts.length === 0; i++) await delay(50);
					assert.equal(posts.length, 1);
					assert.deepEqual(
						posts[0].skill_ids,
						["review"],
						"Recovered submission uses saved skill",
					);
					state = await snapshot();
					assert.deepEqual(state.stored, ["review"]);
				} else if (scenario === "featureDisabled") {
					await page.waitForFunction(async () =>
						(await import("/src/store/useAppStore.js")).useAppStore
							.getState()
							.selectedSkillIds?.includes("review"),
					);
					featureEnabled = false;
					const before = skillRequests;
					await page
						.getByRole("button", { name: "API key status", exact: true })
						.click();
					await page
						.getByRole("menuitem", { name: "Clear API Key", exact: true })
						.click();
					await page.locator("#apiKey").fill("synthetic-replacement-key");
					await page
						.getByRole("button", { name: "Save API Key", exact: true })
						.click();
					await page.getByRole("dialog").waitFor({ state: "hidden" });
					await page.waitForFunction(
						async () =>
							(await import("/src/store/useAppStore.js")).useAppStore.getState()
								.config?.skills_enabled === false,
					);
					for (let i = 0; i < 60 && skillRequests === before; i++)
						await delay(50);
					assert.ok(
						skillRequests > before,
						"Reauthentication reloads disabled catalogue",
					);
					if (viewport.width < 1024)
						await page
							.getByRole("button", { name: "Open consultations", exact: true })
							.click();
					await page
						.getByRole("button", {
							name: "Open consultation: Synthetic consultation",
							exact: true,
						})
						.filter({ visible: true })
						.click();
					await settled();
					assert.deepEqual(
						(await snapshot()).stored,
						["review"],
						"Disabled feature does not erase preference",
					);
					assert.equal(
						await page
							.getByRole("button", { name: "Manage agent skills", exact: true })
							.count(),
						0,
					);
					await send();
					for (let i = 0; i < 60 && posts.length === 0; i++) await delay(50);
					assert.equal(posts.length, 1);
					assert.deepEqual(
						posts[0].skill_ids,
						undefined,
						"Disabled feature omits cached skill IDs (API serializes empty IDs as absent)",
					);
					await page.waitForFunction(
						() => document.querySelector("#prompt-input").value === "",
					);
					assert.deepEqual((await snapshot()).stored, ["review"]);
				} else {
					await page.waitForFunction(async () =>
						(await import("/src/store/useAppStore.js")).useAppStore
							.getState()
							.selectedSkillIds?.includes("review"),
					);
					await page
						.getByRole("button", { name: "Manage agent skills", exact: true })
						.click();
					assert.equal(
						await page
							.locator("#skill-default-review")
							.getAttribute("aria-checked"),
						"true",
					);
					const before = skillRequests;
					catalogue = scenario === "refresh503" ? "503" : scenario;
					await page
						.getByRole("checkbox", { name: "~/.codex/skills", exact: true })
						.click();
					for (let i = 0; i < 60 && skillRequests === before; i++)
						await delay(50);
					assert.ok(
						skillRequests > before,
						"Source toggle refreshes catalogue",
					);
					await page.waitForFunction(async (failure) => {
						const state = (
							await import("/src/store/useAppStore.js")
						).useAppStore.getState();
						return failure
							? state.error || state.skills.length === 0
							: state.selectedSkillIds?.length === 0;
					}, scenario === "refresh503");
					const state = await snapshot();
					if (scenario === "refresh503") {
						assert.deepEqual(state.stored, ["review"]);
						assert.deepEqual(state.selected, ["review"]);
						assert.deepEqual(state.skills, ["review"]);
						assert.equal(
							await page
								.locator("#skill-default-review")
								.getAttribute("aria-checked"),
							"true",
						);
						assert.ok(state.error);
					} else {
						assert.deepEqual(
							state.stored,
							[],
							"Authoritative unavailable skill is pruned",
						);
						assert.deepEqual(state.selected, []);
						if (scenario === "disabled") {
							assert.equal(
								await page
									.locator("#skill-default-review")
									.getAttribute("aria-checked"),
								"false",
							);
							assert.equal(
								await page.locator("#skill-default-review").isDisabled(),
								true,
							);
						}
					}
					assert.equal(state.loaded, true);
				}
				assert.deepEqual(
					unexpected,
					[],
					"Only expected synthetic API requests",
				);
				assert.deepEqual(errors, [], "No uncaught browser errors");
				if (SCREENSHOT_DIR)
					await page.screenshot({
						path: join(
							SCREENSHOT_DIR,
							`skills-${viewport.width}-${scenario}.png`,
						),
						fullPage: true,
					});
				results.push(name);
				console.log(`PASS ${name}`);
			} catch (error) {
				failures.push({ name, error: error.message });
				console.error(`FAIL ${name}: ${error.message}`);
			} finally {
				await context.close();
			}
		}
	}
}
try {
	await Promise.race([
		run(),
		new Promise((_, reject) => {
			deadline = setTimeout(() => {
				stopped = true;
				reject(new Error("180 second browser deadline exceeded"));
			}, 180_000);
		}),
	]);
} finally {
	stopped = true;
	clearTimeout(deadline);
	await browser?.close();
	if (server.exitCode === null) {
		const exited = once(server, "exit");
		server.kill("SIGTERM");
		const force = setTimeout(() => server.kill("SIGKILL"), 3000);
		try {
			await exited;
		} finally {
			clearTimeout(force);
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
