/**
 * Persistence for the two-layer agent skill selection:
 *
 * 1. GLOBAL DEFAULT - the shared skill set that applies to every chat,
 *    edited from the Skills modal ("Default skills").
 * 2. PER-CHAT EXCLUSIONS - skills toggled off for one specific chat only,
 *    persisted per conversation id.
 *
 * getDefault() returns null when nothing was ever stored (so callers can
 * seed from the last applied message), while setDefault([]) stores an empty
 * array so a deliberately cleared default is never resurrected on reload.
 */

const DEFAULT_KEY = "llm_council_skills_default";
const LEGACY_GLOBAL_KEY = "llm_council_skills_global";
const EXCLUSION_PREFIX = "llm_council_skills_exclusions_";

const isUsable = () => typeof window !== "undefined" && !!window.localStorage;

const exclusionKey = (conversationId) => `${EXCLUSION_PREFIX}${conversationId}`;

const parseIds = (raw) => {
	if (!raw) return null;
	try {
		const parsed = JSON.parse(raw);
		if (!Array.isArray(parsed)) return null;
		return parsed.filter((id) => typeof id === "string" && id);
	} catch {
		return null;
	}
};

export const skillSelectionStorage = {
	/**
	 * The global default skill ids, or null when nothing was ever stored.
	 * Falls back to the legacy shared-selection key for one-time migration.
	 * Never throws: malformed JSON or unavailable storage yields null.
	 * @returns {string[] | null}
	 */
	getDefault() {
		if (!isUsable()) return null;
		const stored = parseIds(window.localStorage.getItem(DEFAULT_KEY));
		if (stored !== null) return stored;
		return parseIds(window.localStorage.getItem(LEGACY_GLOBAL_KEY));
	},

	/**
	 * Store the global default skill ids. An empty list is stored as an
	 * explicit empty default, never removed.
	 * @param {string[]} ids - The skill ids to remember
	 */
	setDefault(ids) {
		if (!isUsable()) return;
		const safeIds = Array.isArray(ids)
			? ids.filter((id) => typeof id === "string" && id)
			: [];
		try {
			window.localStorage.setItem(DEFAULT_KEY, JSON.stringify(safeIds));
		} catch {
			// Storage unavailable or over quota - the draft is best-effort.
		}
	},

	/**
	 * Skill ids toggled off for one chat. An empty array means "follow the
	 * default exactly".
	 * @param {string} conversationId - The conversation ID
	 * @returns {string[]}
	 */
	getExclusions(conversationId) {
		if (!isUsable() || !conversationId) return [];
		return (
			parseIds(window.localStorage.getItem(exclusionKey(conversationId))) || []
		);
	},

	/**
	 * Store the per-chat exclusions; an empty list clears them.
	 * @param {string} conversationId - The conversation ID
	 * @param {string[]} ids - The skill ids toggled off for this chat
	 */
	setExclusions(conversationId, ids) {
		if (!isUsable() || !conversationId) return;
		const safeIds = Array.isArray(ids)
			? ids.filter((id) => typeof id === "string" && id)
			: [];
		try {
			if (!safeIds.length) {
				window.localStorage.removeItem(exclusionKey(conversationId));
				return;
			}
			window.localStorage.setItem(
				exclusionKey(conversationId),
				JSON.stringify(safeIds),
			);
		} catch {
			// Storage unavailable or over quota - the draft is best-effort.
		}
	},
};
