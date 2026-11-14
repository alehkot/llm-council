import assert from "node:assert/strict";
import test from "node:test";
import {
	heldRequestDiagnostic,
	requireHeldRequest,
	readOldResult,
	safeRequestLocation,
} from "./authReplacementDiagnostics.mjs";

function diagnostic(oldResult = "pending") {
	return heldRequestDiagnostic({
		scenario: { phase: "request", status: 401, current: true },
		viewport: { width: 390 },
		requests: ["http://localhost:8001/api/conversations"],
		pageErrors: [],
		oldResult,
	});
}

test("capture preserves route identity without collecting diagnostics", async () => {
	const route = {};
	let pending;
	let waits = 0;
	assert.equal(
		await requireHeldRequest({
			getPending: () => pending,
			wait: async (milliseconds) => {
				assert.equal(milliseconds, 50);
				if (++waits === 3) pending = route;
			},
			diagnostic: () => assert.fail("unneeded diagnostic"),
		}),
		route,
	);
	assert.equal(waits, 3);
});

for (const outcome of ["pending", "fulfilled", "AbortError"]) {
	test(`missing capture fails closed with ${outcome} evidence at unchanged 3s`, async () => {
		let elapsed = 0;
		await assert.rejects(
			requireHeldRequest({
				getPending: () => undefined,
				wait: async (milliseconds) => {
					elapsed += milliseconds;
				},
				diagnostic: () => {
					assert.equal(elapsed, 3000);
					return diagnostic(outcome);
				},
			}),
			(error) => {
				assert.match(error.message, /old request must be held/);
				assert.match(error.message, new RegExp(`"oldResult":"${outcome}"`));
				return true;
			},
		);
	});
}

test("safe request evidence omits credentials query hash and unknown paths", () => {
	assert.deepEqual(
		safeRequestLocation(
			"http://username:secret@localhost:8001/api/config?key=secret#secret",
		),
		{ origin: "http://localhost:8001", path: "/api/config" },
	);
	assert.deepEqual(safeRequestLocation("https://secret.example/secret"), {
		origin: "[unexpected origin]",
		path: "[unexpected path]",
	});
});

test("diagnostics cap records and reject arbitrary error names and scenario data", () => {
	const value = heldRequestDiagnostic({
		scenario: { phase: "secret", status: "secret", sameKey: "secret" },
		viewport: { width: "secret" },
		requests: Array(30).fill("http://localhost:8001/api/config?secret"),
		pageErrors: Array(30).fill("TypeError secret payload"),
		oldResult: "secret payload",
		unexpected: Array(30).fill({ origin: "secret", path: "/secret" }),
		armed: true,
	});
	assert.equal(value.includes("secret"), false);
	const parsed = JSON.parse(value);
	assert.equal(parsed.requests.length, 12);
	assert.equal(parsed.pageErrors.length, 8);
	assert.equal(parsed.oldResult, "UnknownError");
	assert.equal(parsed.unexpected.length, 12);
	assert.equal(parsed.armed, true);
});

test("old-result diagnostic bounds hung evaluation and clears its timer", async () => {
	let expire;
	let cleared;
	const result = readOldResult(() => new Promise(() => {}), {
		setTimer: (callback, milliseconds) => {
			assert.equal(milliseconds, 1000);
			expire = callback;
			return 42;
		},
		clearTimer: (timer) => {
			cleared = timer;
		},
	});
	expire();
	assert.equal(await result, "unavailable");
	assert.equal(cleared, 42);
});

test("old-result diagnostic preserves settlement but suppresses raw errors", async () => {
	for (const state of ["pending", "fulfilled", "AbortError"]) {
		assert.equal(await readOldResult(() => state), state);
	}
	assert.equal(
		await readOldResult(() => {
			throw new Error("secret");
		}),
		"unavailable",
	);
});
