import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// Execute the real API module with controlled network/storage seams. Browser
// integration is covered separately; no provider or real credential is used.
const source = readFileSync(new URL("../src/api.js", import.meta.url), "utf8")
	.replace(/import \{ apiKeyStorage \} from [^;]+;/, "")
	.replace("import.meta.env.VITE_API_BASE", '"http://localhost:8001"')
	.replace(/export const /g, "const ");
const deferred = () => {
	let resolve;
	let reject;
	const promise = new Promise((yes, no) => {
		resolve = yes;
		reject = no;
	});
	return { promise, resolve, reject };
};
const reply = (status = 200, retryAfter = null) => ({
	status,
	ok: status === 200,
	headers: { get: () => retryAfter },
	json: async () => [],
});
function harness() {
	let key = "synthetic-key";
	let now = 1000;
	const requests = [];
	const events = [];
	const context = vm.createContext({
		fetch: (url) => {
			const request = { url, ...deferred() };
			requests.push(request);
			return request.promise;
		},
		apiKeyStorage: {
			get: () => key,
			clear: () => {
				key = null;
			},
		},
		window: {
			location: { hostname: "localhost", protocol: "http:" },
			dispatchEvent: (e) => events.push(e.detail),
		},
		CustomEvent: class {
			constructor(_, options) {
				this.detail = options.detail;
			}
		},
		Date: { now: () => now },
	});
	vm.runInContext(`${source}\nglobalThis.testApi = api;`, context);
	return {
		api: context.testApi,
		requests,
		events,
		setKey: (value) => {
			key = value;
		},
		advance: (ms) => {
			now += ms;
		},
	};
}
const flush = () => new Promise((resolve) => setImmediate(resolve));

test("a transient network failure can recover without reloading or resetting credentials", async () => {
	const h = harness();
	const first = h.api.listConversations();
	h.requests[0].reject(new TypeError("offline"));
	await assert.rejects(first, /offline/);
	const retry = h.api.listConversations();
	void retry.catch(() => {}); // Keep baseline failures handled before assertions.
	await flush();
	assert.equal(
		h.requests.length,
		2,
		"retry must start a fresh authentication probe",
	);
	h.requests[1].resolve(reply());
	await flush();
	h.requests[2].resolve(reply());
	await retry;
	assert.equal(
		h.events.length,
		0,
		"network failures are not invalid credentials",
	);
});

test("concurrent callers share the failed probe and share the next retry", async () => {
	const h = harness();
	const batch = [h.api.listConversations(), h.api.listConversations()];
	const settled = Promise.allSettled(batch);
	assert.equal(h.requests.length, 1);
	h.requests[0].reject(new TypeError("offline"));
	assert.ok((await settled).every((result) => result.status === "rejected"));
	const retries = [h.api.listConversations(), h.api.listConversations()];
	for (const retry of retries) void retry.catch(() => {});
	await flush();
	assert.equal(h.requests.length, 2);
	h.requests[1].resolve(reply());
	await flush();
	assert.equal(h.requests.length, 4);
	h.requests[2].resolve(reply());
	h.requests[3].resolve(reply());
	await Promise.all(retries);
});

for (const status of [401, 403, 429]) {
	test(`HTTP ${status} still latches without another network request`, async () => {
		const h = harness();
		const first = h.api.listConversations();
		h.requests[0].resolve(reply(status, status === 429 ? "30" : null));
		await assert.rejects(first, /INVALID_API_KEY|API_KEY_RATE_LIMITED/);
		await assert.rejects(
			h.api.listConversations(),
			/INVALID_API_KEY|API_KEY_RATE_LIMITED/,
		);
		assert.equal(h.requests.length, 1);
	});
}

test("rate-limit expiry still allows a fresh shared probe", async () => {
	const h = harness();
	const first = h.api.listConversations();
	h.requests[0].resolve(reply(429, "30"));
	await assert.rejects(first, /API_KEY_RATE_LIMITED/);
	h.advance(30001);
	const retry = h.api.listConversations();
	assert.equal(h.requests.length, 2);
	h.requests[1].resolve(reply());
	await flush();
	h.requests[2].resolve(reply());
	await retry;
});

test("a failed old probe cannot evict a newer probe after explicit reset", async () => {
	const h = harness();
	const old = h.api.listConversations();
	const oldRejected = assert.rejects(old, /offline/);
	h.api.resetAuthState();
	const fresh = h.api.listConversations();
	h.requests[0].reject(new TypeError("offline"));
	await oldRejected;
	const follower = h.api.listConversations();
	assert.equal(h.requests.length, 2, "follower must reuse the fresh probe");
	h.requests[1].resolve(reply());
	await flush();
	h.requests[2].resolve(reply());
	h.requests[3].resolve(reply());
	await Promise.all([fresh, follower]);
});

test("missing credentials remain latched without network access", async () => {
	const h = harness();
	h.setKey(null);
	await assert.rejects(h.api.listConversations(), /NO_API_KEY/);
	await assert.rejects(h.api.listConversations(), /NO_API_KEY/);
	assert.equal(h.requests.length, 0);
});
