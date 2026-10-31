import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// Execute the production callback with controlled API/store seams, not rendered UI.
const source = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
const clone = (value) => JSON.parse(JSON.stringify(value));
function harness() {
	let resolve;
	let reject;
	const pending = new Promise((yes, no) => {
		resolve = yes;
		reject = no;
	});
	const state = {
		currentConversationId: "A",
		conversations: [{ id: "A" }, { id: "B" }],
		conversationsById: { A: { id: "A" }, B: { id: "B" } },
		deleteTarget: { id: "A", title: "A" },
		error: null,
	};
	const calls = { deleted: [], selected: [], urls: [] };
	const setters = Object.fromEntries(
		["conversations", "conversationsById", "deleteTarget", "error"].map(
			(key) => [
				`set${key[0].toUpperCase()}${key.slice(1)}`,
				(next) => {
					state[key] = typeof next === "function" ? next(state[key]) : next;
				},
			],
		),
	);
	const start = "\tconst confirmDeleteConversation = async () => {";
	const end = "\n\tconst handleClearConversations";
	assert.equal(
		source.split(start).length,
		2,
		"Unique production callback marker",
	);
	const body = start + source.split(start)[1].split(end)[0];
	const selectStart = "\tconst selectConversation = useCallback(";
	const selectEnd = "\n\tconst updateConversationById";
	assert.equal(
		source.split(selectStart).length,
		2,
		"Unique production selection marker",
	);
	const selection =
		selectStart + source.split(selectStart)[1].split(selectEnd)[0];
	const callback = () =>
		vm.runInNewContext(
			`${selection}\n${body}\nconfirmDeleteConversation;`,
			{
				...state,
				...setters,
				useAppStore: { getState: () => state },
				api: {
					deleteConversation: (id) => {
						calls.deleted.push(id);
						return pending;
					},
				},
				useCallback: (callback) => callback,
				setCurrentConversationId: (id) => {
					state.currentConversationId = id;
					calls.selected.push(id);
				},
				updateConversationUrl: (id, replace) =>
					calls.urls.push({ id, replace }),
				console: { error: () => {} },
				handleApiError: () => false,
			},
			{ filename: "App.jsx" },
		);
	return { state, calls, callback, resolve, reject };
}

test("late deletion removes only A from latest list/cache and preserves B selection", async () => {
	const h = harness();
	const running = h.callback()();
	h.state.currentConversationId = "B";
	h.state.conversations = [
		{ id: "C" },
		{ id: "A" },
		{ id: "B", title: "Updated B" },
	];
	h.state.conversationsById = {
		...h.state.conversationsById,
		B: { id: "B", updated: true },
		C: { id: "C" },
	};
	h.resolve();
	await running;
	assert.deepEqual(h.calls.deleted, ["A"]);
	assert.equal(h.state.currentConversationId, "B");
	assert.deepEqual(clone(h.state.conversations), [
		{ id: "C" },
		{ id: "B", title: "Updated B" },
	]);
	assert.deepEqual(clone(h.state.conversationsById), {
		B: { id: "B", updated: true },
		C: { id: "C" },
	});
	assert.deepEqual(h.calls.selected, []);
});

for (const id of ["B", "A"]) {
	test(`late deletion preserves a newer ${id} confirmation dialog`, async () => {
		const h = harness();
		const running = h.callback()();
		const newer = { id, title: "New dialog" };
		h.state.deleteTarget = newer;
		h.resolve();
		await running;
		assert.equal(h.state.deleteTarget, newer);
	});
}

test("deleting still-selected A clears selection and original dialog", async () => {
	const h = harness();
	const running = h.callback()();
	h.resolve();
	await running;
	assert.equal(h.state.currentConversationId, null);
	assert.equal(h.state.deleteTarget, null);
	assert.deepEqual(h.calls.selected, [null]);
	assert.deepEqual(h.calls.urls, [{ id: null, replace: true }]);
	assert.deepEqual(clone(h.state.conversations), [{ id: "B" }]);
});

for (const initial of ["B", null]) {
	test(`selecting the deletion target from ${initial} during DELETE clears that now-deleted selection`, async () => {
		const h = harness();
		h.state.currentConversationId = initial;
		const running = h.callback()();
		h.state.currentConversationId = "A";
		h.resolve();
		await running;
		assert.equal(h.state.currentConversationId, null);
	});
}

test("failed deletion leaves list, cache, selection and newer dialog intact", async () => {
	const h = harness();
	const running = h.callback()();
	h.state.currentConversationId = "B";
	h.state.deleteTarget = { id: "B", title: "B" };
	const expected = clone(h.state);
	h.reject(new Error("Synthetic deletion failure"));
	await running;
	const { error, ...actual } = clone(h.state);
	const { error: ignored, ...before } = expected;
	assert.equal(ignored, null);
	assert.deepEqual(actual, before);
	assert.equal(error.details, "Synthetic deletion failure");
	assert.deepEqual(h.calls.selected, []);
});

test("no confirmation target performs no deletion", async () => {
	const h = harness();
	h.state.deleteTarget = null;
	await h.callback()();
	assert.deepEqual(h.calls.deleted, []);
});
