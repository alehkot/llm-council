/** Bounded, credential-free evidence for the synthetic rendered auth harness. */
const errorNames = new Set([
	"Error",
	"TypeError",
	"ReferenceError",
	"SyntaxError",
	"RangeError",
	"AbortError",
	"TimeoutError",
	"ApiAuthError",
]);
const paths = new Set([
	"/api/config",
	"/api/conversations",
	"/api/skills",
	"/api/consultations/status",
]);

export function safeErrorName(name) {
	return errorNames.has(name) ? name : "UnknownError";
}

export function safeRequestLocation(raw) {
	try {
		const url = new URL(raw);
		return {
			origin:
				url.origin === "http://localhost:8001"
					? url.origin
					: "[unexpected origin]",
			path: paths.has(url.pathname) ? url.pathname : "[unexpected path]",
		};
	} catch {
		return { origin: "[invalid URL]", path: "[invalid URL]" };
	}
}

export function heldRequestDiagnostic({
	scenario,
	viewport,
	requests,
	pageErrors,
	oldResult,
	unexpected = [],
	armed = false,
}) {
	return JSON.stringify({
		phase: scenario.phase === "probe" ? "probe" : "request",
		status: [200, 401, 403, 429].includes(scenario.status)
			? scenario.status
			: "unknown",
		sameKey: scenario.sameKey === true,
		current: scenario.current === true,
		viewport: [390, 1280].includes(viewport.width) ? viewport.width : "unknown",
		armed: armed === true,
		requests: requests.slice(-12).map(safeRequestLocation),
		unexpected: unexpected.slice(-12).map((location) => ({
			origin:
				location.origin === "http://localhost:8001"
					? location.origin
					: "[unexpected origin]",
			path: paths.has(location.path) ? location.path : "[unexpected path]",
		})),
		pageErrors: pageErrors.slice(-8).map(safeErrorName),
		oldResult:
			oldResult === "pending" ||
			oldResult === "fulfilled" ||
			oldResult === "unavailable"
				? oldResult
				: safeErrorName(oldResult),
	});
}

export async function requireHeldRequest({ getPending, wait, diagnostic }) {
	// Preserve the original 60 x 50ms held-request requirement. Diagnostic
	// collection happens only on failure and cannot extend the capture window.
	for (let i = 0; i < 60 && !getPending(); i++) await wait(50);
	const pending = getPending();
	if (!pending) {
		throw new Error(
			`old request must be held before replacement: ${await diagnostic()}`,
		);
	}
	return pending;
}

export async function readOldResult(
	read,
	{ setTimer = setTimeout, clearTimer = clearTimeout } = {},
) {
	let timer;
	try {
		return await Promise.race([
			Promise.resolve()
				.then(read)
				.catch(() => "unavailable"),
			new Promise((resolve) => {
				timer = setTimer(() => resolve("unavailable"), 1000);
			}),
		]);
	} finally {
		clearTimer(timer);
	}
}
