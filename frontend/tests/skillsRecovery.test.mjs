import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// Exercise production loading and normalization, including persistent defaults.
// The rendered companion verifies the actual controls and outgoing requests.
const source = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
const clone = (value) => JSON.parse(JSON.stringify(value));
const helperSource = source.slice(
	source.indexOf("function normalizeSkillIds("),
	source.indexOf("// The read-only applied record"),
);

function harness({ loaded = false, outcome, handled = false } = {}) {
	const state = {
		skills: loaded ? [{ id: "review", enabled: true }] : [],
		skillsLoaded: loaded,
		selected: loaded ? ["review"] : null,
		persisted: ["review"],
		sources: { codex: true, claude: false },
		error: null,
	};
	const context = vm.createContext({
		console: { error() {} },
		useCallback: (callback) => callback,
		useEffect: (callback) => callback(),
		api: { listSkills: async () => outcome() },
		handleApiError: () => handled,
		setSkills: (skills) => {
			state.skills = clone(skills);
		},
		setSkillsLoaded: (loaded) => {
			state.skillsLoaded = loaded;
		},
		setSkillSources: (sources) => {
			state.sources = clone(sources);
		},
		setSelectedSkillIds: (ids) => {
			state.selected = clone(ids);
		},
		setError: (error) => {
			state.error = clone(error);
		},
		skillSelectionStorage: {
			getDefault: () => state.persisted,
			setDefault: (ids) => {
				state.persisted = clone(ids);
			},
		},
		conversations: [],
		conversationsById: {},
		findLastRequestedSkillIds: () => [],
	});
	vm.runInContext(helperSource, context);
	const loading = source.slice(
		source.indexOf("\tconst loadSkills = useCallback("),
		source.indexOf("\n\t// Load conversations on mount"),
	);
	const load = vm.runInContext(`${loading}\nloadSkills;`, context);
	const effects = source.slice(
		source.indexOf("\t// The GLOBAL DEFAULT skill selection"),
		source.indexOf("\t// The effective per-chat selection"),
	);
	async function refresh(nextOutcome) {
		if (nextOutcome) outcome = nextOutcome;
		await load();
		Object.assign(context, {
			skillsLoaded: state.skillsLoaded,
			rawSelectedSkillIds: state.selected,
			availableSkillIds: new Set(
				state.skills.filter((s) => s.enabled !== false).map((s) => s.id),
			),
		});
		vm.runInContext(effects, context);
	}
	return { state, refresh };
}

for (const loaded of [false, true]) {
	for (const failure of [
		"network",
		"503",
		"404",
		"auth",
		"aborted",
		"malformed",
	]) {
		test(`${failure} ${loaded ? "refresh" : "initial load"} preserves saved skill defaults`, async () => {
			const h = harness({
				loaded,
				handled: ["auth", "aborted"].includes(failure),
				outcome: () => {
					if (failure === "malformed") return { skills: null };
					const error = new Error(
						failure === "auth" ? "INVALID_API_KEY" : failure,
					);
					if (failure === "aborted") error.name = "AbortError";
					if (failure === "404") error.status = 404;
					throw error;
				},
			});
			await h.refresh();
			assert.deepEqual(h.state.persisted, ["review"]);
			assert.equal(h.state.skillsLoaded, loaded);
			assert.deepEqual(h.state.selected, loaded ? ["review"] : null);
			assert.deepEqual(h.state.sources, { codex: true, claude: false });
			assert.equal(
				Boolean(h.state.error),
				!["auth", "aborted", "404"].includes(failure),
			);
			await h.refresh(() => ({ skills: [{ id: "review", enabled: true }] }));
			assert.deepEqual(h.state.selected, ["review"]);
			assert.deepEqual(h.state.persisted, ["review"]);
			assert.equal(h.state.skillsLoaded, true);
		});
	}
}

for (const skills of [[], [{ id: "review", enabled: false }]]) {
	test(`successful ${skills.length ? "disabled" : "empty"} catalog still prunes defaults`, async () => {
		const h = harness({ loaded: true, outcome: () => ({ skills }) });
		await h.refresh();
		assert.deepEqual(h.state.selected, []);
		assert.deepEqual(h.state.persisted, []);
		assert.equal(h.state.skillsLoaded, true);
	});
}

const apiSource = readFileSync(
	new URL("../src/api.js", import.meta.url),
	"utf8",
);
for (const status of [404, 503]) {
	test(`skills API preserves HTTP ${status} for optional-endpoint handling`, async () => {
		const method = apiSource.slice(
			apiSource.indexOf("\tasync listSkills() {"),
			apiSource.indexOf("\n\tasync setSkillSources("),
		);
		const list = vm.runInNewContext(`({${method}}).listSkills`, {
			apiFetch: async () => ({ ok: false, status }),
			parseErrorDetail: async () => "Skills unavailable",
		});
		await assert.rejects(
			list(),
			(error) =>
				error.status === status && error.message === "Skills unavailable",
		);
	});
}
