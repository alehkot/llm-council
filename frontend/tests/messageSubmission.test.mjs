import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// Run the production callback, controlling HTTP completion and the live store.
// Complemented by the rendered send-navigation browser regression.
const source = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
const normalizeSkillIds = vm.runInNewContext(
	`function normalizeSkillIds${source.split("function normalizeSkillIds")[1].split("function sameSkillIds")[0]}\nnormalizeSkillIds;`,
);
const clone = (value) => JSON.parse(JSON.stringify(value));
const deferred = () => {
	let resolve;
	let reject;
	const promise = new Promise((yes, no) => {
		resolve = yes;
		reject = no;
	});
	return { promise, resolve, reject };
};

function harness(active = false) {
	const pending = deferred();
	const state = {
		currentConversationId: "A",
		isLoading: active,
		consultationStatus: active
			? { status: "stage1", consultation_id: "A-existing" }
			: null,
		messageQueue: active ? [{ id: "A-queued", content: "previous" }] : [],
		currentInteractivePrompt: null,
		interactiveSessionId: null,
		error: null,
		conversations: [
			{ id: "A", message_count: 0 },
			{ id: "B", message_count: 0 },
		],
		conversationsById: {
			A: { id: "A", messages: [] },
			B: { id: "B", messages: [] },
		},
	};
	const calls = { requests: [], errors: [] };
	const setters = Object.fromEntries(
		[
			"isLoading",
			"consultationStatus",
			"messageQueue",
			"currentInteractivePrompt",
			"interactiveSessionId",
			"error",
			"conversations",
		].map((key) => [
			`set${key[0].toUpperCase()}${key.slice(1)}`,
			(next) => {
				state[key] = typeof next === "function" ? next(state[key]) : next;
			},
		]),
	);
	const context = {
		...setters,
		currentConversationId: state.currentConversationId,
		isLoading: state.isLoading,
		consultationStatus: state.consultationStatus,
		normalizeSkillIds,
		selectedSkillIds: null,
		availableSkillIds: new Set(["review"]),
		useAppStore: { getState: () => state },
		requestPermission: () => {},
		updateConversationById: (id, update) => update(state.conversationsById[id]),
		isActiveConsultationStatus: (status) =>
			Boolean(
				status && !["complete", "failed", "none"].includes(status.status),
			),
		api: {
			sendMessage: (...args) => {
				calls.requests.push(args);
				return pending.promise;
			},
		},
		handleApiError: (error) => {
			calls.errors.push(error.message);
			return false;
		},
		console: { error: () => {} },
	};
	const start =
		"\tconst handleSendMessage = async (content, negativePrompt, skillIds) => {";
	const end = "\n\treturn (\n\t\t<>";
	assert.equal(
		source.split(start).length,
		2,
		"Unique production callback marker",
	);
	const callback = vm.runInNewContext(
		`${start}${source.split(start)[1].split(end)[0]}\nhandleSendMessage;`,
		context,
		{ filename: "App.jsx" },
	);
	const selectB = (loading) =>
		Object.assign(state, {
			currentConversationId: "B",
			isLoading: loading,
			consultationStatus: loading
				? { status: "interactive", consultation_id: "B-run" }
				: null,
			messageQueue: [{ id: "B-queued", content: "B question" }],
			currentInteractivePrompt: loading ? { prompt_id: "B-prompt" } : null,
			interactiveSessionId: loading ? "B-session" : null,
			error: { message: "Existing B notice" },
		});
	const controls = () =>
		clone({
			loading: state.isLoading,
			status: state.consultationStatus,
			queue: state.messageQueue,
			prompt: state.currentInteractivePrompt,
			session: state.interactiveSessionId,
			error: state.error,
		});
	return { state, pending, calls, callback, selectB, controls };
}

for (const loading of [false, true]) {
	for (const outcome of ["started", "queued", "failed"]) {
		test(`late ${outcome} A result preserves ${loading ? "active" : "idle"} B controls`, async () => {
			const h = harness(outcome === "queued");
			const running = h.callback("A question", "A review");
			h.selectB(loading);
			const expected = h.controls();
			if (outcome === "failed") h.pending.reject(new Error("A network error"));
			else h.pending.resolve({ status: outcome, consultation_id: "A-new" });
			assert.equal(
				await running,
				outcome !== "failed",
				"Report acceptance to the composer even after navigation",
			);
			assert.deepEqual(h.controls(), expected);
			assert.deepEqual(clone(h.calls.requests), [
				["A", "A question", "A review", []],
			]);
			assert.deepEqual(h.calls.errors, []);
			assert.deepEqual(h.state.conversationsById.B.messages, []);
			if (outcome === "started") {
				assert.equal(
					h.state.conversationsById.A.messages[1].metadata.consultation_id,
					"A-new",
				);
				assert.equal(h.state.conversations[0].message_count, 2);
			} else {
				assert.deepEqual(h.state.conversationsById.A.messages, []);
				assert.equal(h.state.conversations[0].message_count, 0);
			}
		});
	}
}

test("current accepted submission still starts polling and retains its optimistic pair", async () => {
	const h = harness();
	const running = h.callback("A question");
	h.pending.resolve({ status: "started", consultation_id: "A-new" });
	assert.equal(await running, true);
	assert.equal(h.state.isLoading, true);
	assert.deepEqual(clone(h.state.consultationStatus), {
		status: "pending",
		consultation_id: "A-new",
	});
	assert.equal(h.state.conversationsById.A.messages[0].content, "A question");
	assert.equal(
		h.state.conversationsById.A.messages[1].metadata.status,
		"pending",
	);
});

test("submission retains explicit skills while navigating to another conversation", async () => {
	const h = harness();
	const running = h.callback("A question", "A review", ["review"]);
	h.selectB(false);
	const expected = h.controls();
	h.pending.resolve({ status: "started", consultation_id: "A-new" });
	assert.equal(await running, true);
	assert.deepEqual(clone(h.calls.requests), [
		["A", "A question", "A review", ["review"]],
	]);
	assert.deepEqual(h.controls(), expected);
});

test("current queued submission still restores active status and adds its queue entry", async () => {
	const h = harness(true);
	const running = h.callback("A question");
	h.pending.resolve({ status: "queued", consultation_id: "A-new" });
	assert.equal(await running, true);
	assert.equal(h.state.isLoading, true);
	assert.equal(h.state.consultationStatus.consultation_id, "A-existing");
	assert.deepEqual(clone(h.state.messageQueue.map((message) => message.id)), [
		"A-queued",
		"A-new",
	]);
	assert.equal(h.state.messageQueue[1].content, "A question");
	assert.deepEqual(h.state.conversationsById.A.messages, []);
});

test("current failure still rolls back and reports a retryable error", async () => {
	const h = harness();
	const running = h.callback("A question");
	h.pending.reject(new Error("Network unavailable"));
	assert.equal(await running, false);
	assert.equal(h.state.isLoading, false);
	assert.equal(h.state.error.message, "Failed to send message.");
	assert.equal(h.state.error.details, "Network unavailable");
	assert.deepEqual(h.calls.errors, ["Network unavailable"]);
	assert.deepEqual(h.state.conversationsById.A.messages, []);
	assert.equal(h.state.conversations[0].message_count, 0);
});

test("returning to A before its submission completes applies the result to A", async () => {
	const h = harness();
	const running = h.callback("A question");
	h.selectB(false);
	h.state.currentConversationId = "A";
	h.pending.resolve({ status: "started", consultation_id: "A-new" });
	assert.equal(await running, true);
	assert.equal(h.state.isLoading, true);
	assert.equal(h.state.consultationStatus.consultation_id, "A-new");
});
