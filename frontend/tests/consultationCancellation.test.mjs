import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// Exercise the production callback with controlled API promises and store seams.
// This is not a browser/UI rendering test; mounted React checks are complementary.
const source = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
const deferred = () => {
	let resolve;
	let reject;
	const promise = new Promise((yes, no) => {
		resolve = yes;
		reject = no;
	});
	return { promise, resolve, reject };
};
const flush = () => new Promise((resolve) => setImmediate(resolve));
const clone = (value) => JSON.parse(JSON.stringify(value));

function harness(overrides = {}) {
	const state = {
		currentConversationId: "A",
		cancellingConversations: {},
		isCancelling: false, // Compatibility seam to demonstrate the old callback's bug.
		isLoading: true,
		consultationStatus: { status: "stage1", consultation_id: "A-run" },
		messageQueue: [{ id: "A-queued" }],
		currentInteractivePrompt: { prompt_id: "A-prompt" },
		interactiveSessionId: "A-session",
		error: null,
		...overrides.state,
	};
	const calls = {
		cancel: [],
		cleared: [],
		snapshots: [],
		conversation: [],
		lists: 0,
		errors: [],
	};
	const setters = Object.fromEntries(
		[
			"cancellingConversations",
			"isCancelling",
			"isLoading",
			"consultationStatus",
			"messageQueue",
			"currentInteractivePrompt",
			"interactiveSessionId",
			"error",
		].map((key) => [
			`set${key[0].toUpperCase()}${key.slice(1)}`,
			(next) => {
				state[key] = typeof next === "function" ? next(state[key]) : next;
			},
		]),
	);
	const api = {
		cancelConsultation: async (id) => {
			calls.cancel.push(id);
			return overrides.cancel?.(id) ?? { status: "ok" };
		},
		getConversation: async (id) => {
			calls.conversation.push(id);
			return overrides.conversation?.(id) ?? { id, messages: [] };
		},
	};
	const applyConversationSnapshot = (conversation) =>
		calls.snapshots.push(conversation);
	const context = {
		...setters,
		useAppStore: { getState: () => state },
		useCallback: (callback) => callback,
		api,
		console: { error: () => {} },
		clearStreamingConsultation: (id) => calls.cleared.push(id),
		applyConversationSnapshot,
		loadConversation: async (id) =>
			applyConversationSnapshot(await api.getConversation(id)),
		loadConversations: async () => {
			calls.lists++;
		},
		handleApiError: (error) => {
			calls.errors.push(error.message);
			return false;
		},
	};
	const start = "\tconst handleCancelConsultation = useCallback(async () => {";
	const end = "\n\tconst handleSelectConversation";
	assert.equal(
		source.split(start).length,
		2,
		"Unique production callback marker",
	);
	const body = start + source.split(start)[1].split(end)[0];
	const callback = () =>
		vm.runInNewContext(
			`${body}\nhandleCancelConsultation;`,
			{
				...context,
				currentConversationId: state.currentConversationId,
				isCancelling: state.isCancelling,
			},
			{ filename: "App.jsx" },
		);
	const select = (id) =>
		Object.assign(state, {
			currentConversationId: id,
			isLoading: true,
			consultationStatus: { status: "stage3", consultation_id: `${id}-run` },
			messageQueue: [{ id: `${id}-queued` }],
			currentInteractivePrompt: { prompt_id: `${id}-prompt` },
			interactiveSessionId: `${id}-session`,
			error: null,
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
	return { state, calls, callback, select, controls };
}

test("late Stop A success preserves B controls and refreshes only A cache", async () => {
	const pending = deferred();
	const h = harness({ cancel: () => pending.promise });
	const running = h.callback()();
	h.select("B");
	const expected = h.controls();
	pending.resolve({ status: "ok" });
	await running;
	assert.deepEqual(h.controls(), expected);
	assert.deepEqual(h.calls.cancel, ["A"]);
	assert.deepEqual(h.calls.cleared, ["A"]);
	assert.deepEqual(h.calls.conversation, ["A"]);
	assert.equal(h.calls.snapshots[0].id, "A");
	assert.equal(h.calls.lists, 1);
});

for (const stage of ["cancel", "conversation"]) {
	test(`obsolete ${stage} failure does not replace B's error state`, async () => {
		const pending = deferred();
		const h = harness({ [stage]: () => pending.promise });
		const running = h.callback()();
		await flush();
		h.select("B");
		const expected = h.controls();
		pending.reject(new Error("A request failed"));
		await running;
		assert.deepEqual(h.controls(), expected);
		assert.deepEqual(h.calls.errors, []);
		assert.deepEqual(clone(h.state.cancellingConversations), {});
	});
}

test("current Stop success still clears active controls and completes refresh", async () => {
	const h = harness();
	await h.callback()();
	assert.deepEqual(h.controls(), {
		loading: false,
		status: null,
		queue: [],
		prompt: null,
		session: null,
		error: null,
	});
	assert.deepEqual(h.calls.cleared, ["A"]);
	assert.equal(h.calls.snapshots[0].id, "A");
	assert.deepEqual(clone(h.state.cancellingConversations), {});
});

for (const stage of ["cancel", "conversation"]) {
	test(`current ${stage} failure is reported and releases only its busy entry`, async () => {
		const h = harness({
			state: { cancellingConversations: { B: true } },
			[stage]: async () => {
				throw new Error("current request failed");
			},
		});
		await h.callback()();
		assert.deepEqual(h.calls.errors, ["current request failed"]);
		assert.equal(h.state.error.details, "current request failed");
		assert.deepEqual(clone(h.state.cancellingConversations), { B: true });
	});
}

test("duplicate clicks before a React render issue one Stop request", async () => {
	const pending = deferred();
	const h = harness({ cancel: () => pending.promise });
	const cancel = h.callback();
	const first = cancel();
	const second = cancel();
	pending.resolve({ status: "ok" });
	await Promise.all([first, second]);
	assert.deepEqual(h.calls.cancel, ["A"]);
});

test("B can stop independently while A is stopping, and A cleanup cannot unlock B", async () => {
	const a = deferred();
	const b = deferred();
	const h = harness({ cancel: (id) => (id === "A" ? a : b).promise });
	const first = h.callback()();
	h.select("B");
	const second = h.callback()();
	assert.deepEqual(h.calls.cancel, ["A", "B"]);
	assert.deepEqual(clone(h.state.cancellingConversations), {
		A: true,
		B: true,
	});
	const expected = h.controls();
	a.resolve({ status: "ok" });
	await first;
	assert.deepEqual(h.controls(), expected);
	assert.deepEqual(clone(h.state.cancellingConversations), { B: true });
	b.resolve({ status: "ok" });
	await second;
	assert.deepEqual(clone(h.state.cancellingConversations), {});
	assert.equal(h.state.isLoading, false);
});

test("returning to A while its Stop is pending does not allow another Stop", async () => {
	const pending = deferred();
	const h = harness({ cancel: () => pending.promise });
	const first = h.callback()();
	h.select("B");
	h.select("A");
	await h.callback()();
	pending.resolve({ status: "ok" });
	await first;
	assert.deepEqual(h.calls.cancel, ["A"]);
	assert.equal(h.state.isLoading, false);
});

test("late completion after deselection does not restore selected-chat controls", async () => {
	const pending = deferred();
	const h = harness({ cancel: () => pending.promise });
	const running = h.callback()();
	h.select(null);
	const expected = h.controls();
	pending.resolve({ status: "ok" });
	await running;
	assert.deepEqual(h.controls(), expected);
});

test("no conversation selected sends no Stop request", async () => {
	const h = harness({ state: { currentConversationId: null } });
	await h.callback()();
	assert.deepEqual(h.calls.cancel, []);
});
