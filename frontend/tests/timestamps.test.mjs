import assert from "node:assert/strict";
import test from "node:test";
import { parseTimestamp } from "../src/lib/utils.js";

test("saved UTC timestamps and explicit offsets resolve to the same local time", (t) => {
	const previousZone = process.env.TZ;
	process.env.TZ = "America/New_York";
	t.after(() => {
		if (previousZone === undefined) delete process.env.TZ;
		else process.env.TZ = previousZone;
	});

	for (const [day, localHour] of [
		["2026-11-17", 14],
		["2026-07-17", 15],
	]) {
		const utc = `${day}T19:30:00.123456`;
		for (const timestamp of [utc, `${utc}Z`, `${day}T21:30:00.123456+02:00`]) {
			const parsed = parseTimestamp(timestamp);
			assert.equal(parsed.toISOString(), `${day}T19:30:00.123Z`);
			assert.equal(parsed.getHours(), localHour);
		}
	}
});
