import assert from "node:assert/strict";
import test from "node:test";
import { client } from "@agentclientprotocol/sdk";
import { createPiAgent } from "./pi-acp.mjs";

function fixture(overrides = {}) {
	const calls = [];
	const model = {
		id: "native",
		name: "Native",
		provider: "provider",
		baseUrl: "https://native.invalid",
	};
	const provider = {
		auth: { oauth: { isSubscription: true } },
		streamSimple: (selected, context, options) => {
			calls.push({ selected, context, options });
			const result = {
				provider: "provider",
				model: "native",
				stopReason: "stop",
				content: [
					{ type: "thinking", thinking: "Private reasoning" },
					{ type: "text", text: "Answer" },
				],
				...overrides.result,
			};
			return {
				async *[Symbol.asyncIterator]() {
					if (overrides.wait)
						await new Promise((resolve) =>
							options.signal.addEventListener("abort", resolve, { once: true }),
						);
					yield { type: "text_delta", delta: "Answer" };
				},
				result: async () => result,
			};
		},
	};
	const runtime = {
		getProvider: () => (overrides.nonSubscription ? { auth: {} } : provider),
		getModel: (_, id) => (id === model.id ? model : undefined),
		checkAuth: async () => ({ type: overrides.authType ?? "oauth" }),
		getAuth: async () => {
			if (overrides.error)
				throw Object.assign(new Error("secret-token-detail"), {
					code: overrides.error,
				});
			return {
				source: overrides.source ?? "OAuth",
				auth: { apiKey: "oauth-token" },
			};
		},
	};
	const updates = [];
	const app = client().onNotification("*", ({ params }) => {
		updates.push(params);
	});
	return { runtime, calls, updates, app };
}

async function initializeSession(connection) {
	await connection.request("initialize", {
		protocolVersion: 1,
		clientCapabilities: {},
	});
	return connection.request("session/new", {
		cwd: process.cwd(),
		mcpServers: [],
	});
}

test("Pi verifies native OAuth and preserves text without tools or reasoning", async () => {
	const f = fixture();
	await f.app.connectWith(
		createPiAgent(f.runtime, "provider/native"),
		async (connection) => {
			const session = await initializeSession(connection);
			const selected = await connection.request("session/set_config_option", {
				sessionId: session.sessionId,
				configId: "model",
				value: "provider/native",
			});
			assert.equal(selected.configOptions[0].currentValue, "provider/native");
			const response = await connection.request("session/prompt", {
				sessionId: session.sessionId,
				prompt: [{ type: "text", text: "Question ☃" }],
			});
			assert.equal(response.stopReason, "end_turn");
		},
	);
	assert.deepEqual(f.calls[0].context.tools, []);
	assert.equal(f.calls[0].context.messages[0].content, "Question ☃");
	assert.equal(f.calls[0].options.apiKey, "oauth-token");
	assert.equal(
		f.updates
			.filter((u) => u.authStatus)
			.every((u) => u.authStatus.kind === "account"),
		true,
	);
	assert.deepEqual(
		f.updates.filter((u) => u.update).map((u) => u.update.content.text),
		["Answer"],
	);
});

test("Pi refuses API auth, non-subscription OAuth, changed auth, and raw errors", async () => {
	for (const overrides of [
		{ authType: "api_key" },
		{ nonSubscription: true },
		{ source: "API_KEY" },
		{ error: "oauth" },
		{ error: "auth" },
		{ error: "other" },
	]) {
		const f = fixture(overrides);
		await f.app.connectWith(
			createPiAgent(f.runtime, "provider/native"),
			async (connection) => {
				await assert.rejects(
					async () => {
						const session = await initializeSession(connection);
						await connection.request("session/prompt", {
							sessionId: session.sessionId,
							prompt: [{ type: "text", text: "Question" }],
						});
					},
					(error) => !JSON.stringify(error).includes("secret-token-detail"),
				);
			},
		);
		assert.equal(f.calls.length, 0);
	}
});

test("Pi refuses defaults, unknown models, model changes, and wrong sessions", async () => {
	for (const id of ["default", "provider/", "provider/default"])
		assert.throws(() => createPiAgent(fixture().runtime, id));
	const f = fixture();
	await f.app.connectWith(
		createPiAgent(f.runtime, "provider/missing"),
		async (connection) => {
			await assert.rejects(() => initializeSession(connection));
		},
	);
	await f.app.connectWith(
		createPiAgent(f.runtime, "provider/native"),
		async (connection) => {
			const session = await initializeSession(connection);
			await assert.rejects(() =>
				connection.request("session/set_config_option", {
					sessionId: session.sessionId,
					configId: "model",
					value: "provider/other",
				}),
			);
			await assert.rejects(() =>
				connection.request("session/prompt", {
					sessionId: "wrong",
					prompt: [{ type: "text", text: "Question" }],
				}),
			);
		},
	);
	assert.equal(f.calls.length, 0);
});

test("Pi does not turn failed, partial, empty, tool, or substituted responses into success", async () => {
	for (const result of [
		{ stopReason: "error", errorMessage: "secret-token-detail" },
		{ stopReason: "length" },
		{ stopReason: "aborted" },
		{ content: [] },
		{ content: [{ type: "toolCall", name: "bash" }] },
		{ model: "other" },
		{ provider: "other" },
	]) {
		const f = fixture({ result });
		await f.app.connectWith(
			createPiAgent(f.runtime, "provider/native"),
			async (connection) => {
				const session = await initializeSession(connection);
				await assert.rejects(() =>
					connection.request("session/prompt", {
						sessionId: session.sessionId,
						prompt: [{ type: "text", text: "Question" }],
					}),
				);
			},
		);
		assert.equal(f.updates.filter((u) => u.update).length, 0);
	}
});

test("Pi cancellation aborts the native generation", async () => {
	const f = fixture({ wait: true });
	await f.app.connectWith(
		createPiAgent(f.runtime, "provider/native"),
		async (connection) => {
			const session = await initializeSession(connection);
			const prompt = connection.request("session/prompt", {
				sessionId: session.sessionId,
				prompt: [{ type: "text", text: "Question" }],
			});
			while (!f.calls.length)
				await new Promise((resolve) => setImmediate(resolve));
			await connection.notify("session/cancel", {
				sessionId: session.sessionId,
			});
			assert.equal((await prompt).stopReason, "cancelled");
		},
	);
	assert.equal(f.calls[0].options.signal.aborted, true);
	assert.equal(f.updates.filter((u) => u.update).length, 0);
});
