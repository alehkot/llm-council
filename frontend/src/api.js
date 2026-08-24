/**
 * API client for the LLM Council backend.
 */

import { apiKeyStorage } from "./utils/apiKeyStorage";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8001";
const API_KEY_HEADER = "X-API-Key";
export const API_AUTH_ERROR_EVENT = "llm-council:api-auth-error";

const pathSegment = (value) => encodeURIComponent(String(value));
const conversationPath = (conversationId) =>
	`/api/conversations/${pathSegment(conversationId)}`;
const skillPath = (skillId) => `/api/skills/${pathSegment(skillId)}`;

const notifyApiAuthError = (message, detail = {}) => {
	if (
		typeof window === "undefined" ||
		typeof window.dispatchEvent !== "function"
	)
		return;
	window.dispatchEvent(
		new CustomEvent(API_AUTH_ERROR_EVENT, {
			detail: { message, ...detail },
		}),
	);
};

/**
 * Check if we're in a secure context (HTTPS or localhost).
 * API requests should only be made in secure contexts to protect the API key.
 */
const isSecureContext = () => {
	const hostname = window.location.hostname;
	const isLocalhost = ["localhost", "127.0.0.1", "::1"].includes(hostname);
	const isHttps = window.location.protocol === "https:";
	return isLocalhost || isHttps;
};

/**
 * Auth failure latch. Once the backend rejects the key (401/403), rate-limits it
 * (429), or no key is stored, we remember the failure and short-circuit every
 * subsequent request without touching the network. This stops polling loops from
 * hammering the backend and tripping the server-side brute-force lockout. The
 * latch clears when the user submits a new key (or, for 429, once the lockout
 * window expires).
 */
let authFailure = null;

const makeAuthError = (type, retryAfterSeconds) => {
	const error = new Error(type);
	if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0) {
		error.retryAfterSeconds = retryAfterSeconds;
	}
	return error;
};

const throwAuthError = (type, retryAfterSeconds) => {
	const error = makeAuthError(type, retryAfterSeconds);
	notifyApiAuthError(type, {
		retryAfterSeconds: error.retryAfterSeconds,
	});
	throw error;
};

/**
 * Single-flight auth probe. The first request of a session is sent alone (as a
 * cheap GET /api/config) and every concurrent request waits for it instead of
 * firing in parallel. A wrong key therefore costs the backend exactly one
 * failed attempt, not a burst of many that could trip the brute-force lockout.
 * The probe is cached for the session and cleared whenever the auth latch is
 * cleared.
 */
let authProbe = null;

const probeAuth = () => {
	if (authProbe) return authProbe;
	authProbe = (async () => {
		const apiKey = apiKeyStorage.get();
		if (!apiKey) {
			authFailure = { type: "NO_API_KEY" };
			throwAuthError("NO_API_KEY");
		}

		const response = await fetch(`${API_BASE}/api/config`, {
			headers: { [API_KEY_HEADER]: apiKey },
		});

		if (response.status === 429) {
			const retryAfter = response.headers.get("Retry-After");
			const retryAfterSeconds = retryAfter ? Number(retryAfter) : null;
			authFailure = {
				type: "API_KEY_RATE_LIMITED",
				retryAfterSeconds,
				expiresAt:
					Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0
						? Date.now() + retryAfterSeconds * 1000
						: undefined,
			};
			throwAuthError("API_KEY_RATE_LIMITED", retryAfterSeconds);
		}

		if (response.status === 401 || response.status === 403) {
			apiKeyStorage.clear();
			authFailure = { type: "INVALID_API_KEY" };
			throwAuthError("INVALID_API_KEY");
		}
	})();
	return authProbe;
};

/**
 * Make an API request with the stored API key.
 * Throws typed errors for the UI to handle:
 * - 'NO_API_KEY' - No API key in storage
 * - 'INVALID_API_KEY' - Backend rejected the key (401/403)
 * - 'API_KEY_RATE_LIMITED' - Too many invalid attempts (429)
 * - 'INSECURE_CONTEXT' - Not HTTPS (except localhost)
 */
const apiFetch = async (path, options = {}) => {
	if (!isSecureContext()) {
		notifyApiAuthError("INSECURE_CONTEXT");
		throw new Error("INSECURE_CONTEXT");
	}

	// Don't hit the network again once we know auth is broken.
	if (authFailure) {
		if (authFailure.expiresAt && Date.now() >= authFailure.expiresAt) {
			authFailure = null;
			authProbe = null;
		} else {
			throwAuthError(authFailure.type, authFailure.retryAfterSeconds);
		}
	}

	// Collapse parallel requests behind one auth check.
	await probeAuth();

	const apiKey = apiKeyStorage.get();
	if (!apiKey) {
		authFailure = { type: "NO_API_KEY" };
		throwAuthError("NO_API_KEY");
	}

	const response = await fetch(`${API_BASE}${path}`, {
		...options,
		headers: {
			...options.headers,
			[API_KEY_HEADER]: apiKey,
		},
	});

	if (response.status === 429) {
		const retryAfter = response.headers.get("Retry-After");
		const retryAfterSeconds = retryAfter ? Number(retryAfter) : null;
		authFailure = {
			type: "API_KEY_RATE_LIMITED",
			retryAfterSeconds,
			expiresAt:
				Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0
					? Date.now() + retryAfterSeconds * 1000
					: undefined,
		};
		throwAuthError("API_KEY_RATE_LIMITED", retryAfterSeconds);
	}

	// Auto-clear on auth failure and throw typed error
	if (response.status === 401 || response.status === 403) {
		apiKeyStorage.clear();
		authFailure = { type: "INVALID_API_KEY" };
		throwAuthError("INVALID_API_KEY");
	}

	return response;
};

/**
 * Parse filename from Content-Disposition header.
 */
const parseFilenameFromContentDisposition = (headerValue) => {
	if (!headerValue) return null;

	const utf8Match = headerValue.match(/filename\*=UTF-8''([^;]+)/i);
	if (utf8Match && utf8Match[1]) {
		try {
			return decodeURIComponent(utf8Match[1]);
		} catch {
			return utf8Match[1];
		}
	}

	const simpleMatch = headerValue.match(/filename="?([^";]+)"?/i);
	return simpleMatch && simpleMatch[1] ? simpleMatch[1] : null;
};

const parseErrorDetail = async (response, fallback) => {
	const payload = await response.json().catch(() => null);
	const detail = payload?.detail;
	if (typeof detail === "string" && detail.trim()) {
		return detail;
	}
	if (Array.isArray(detail)) {
		const messages = detail
			.map((item) => item?.msg || item?.message || null)
			.filter(Boolean);
		if (messages.length) {
			return messages.join("; ");
		}
	}
	return fallback;
};

export const api = {
	/**
	 * List all conversations.
	 */
	async listConversations() {
		const response = await apiFetch("/api/conversations");
		if (!response.ok) {
			throw new Error("Failed to list conversations");
		}
		return response.json();
	},

	/**
	 * Create a new conversation.
	 */
	async createConversation() {
		const response = await apiFetch("/api/conversations", {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
			},
			body: JSON.stringify({}),
		});
		if (!response.ok) {
			throw new Error("Failed to create conversation");
		}
		return response.json();
	},

	/**
	 * Get a specific conversation.
	 */
	async getConversation(conversationId) {
		const response = await apiFetch(conversationPath(conversationId));
		if (!response.ok) {
			throw new Error("Failed to get conversation");
		}
		return response.json();
	},

	/**
	 * Delete a conversation.
	 */
	async deleteConversation(conversationId) {
		const response = await apiFetch(conversationPath(conversationId), {
			method: "DELETE",
		});
		if (!response.ok) {
			throw new Error("Failed to delete conversation");
		}
		return response.json();
	},

	/**
	 * Delete all conversations.
	 */
	async clearConversations() {
		const response = await apiFetch("/api/conversations", {
			method: "DELETE",
		});
		if (!response.ok) {
			throw new Error("Failed to delete conversations");
		}
		return response.json();
	},

	/**
	 * Get backend config flags.
	 */
	async getConfig() {
		const response = await apiFetch("/api/config");
		if (!response.ok) {
			throw new Error("Failed to get config");
		}
		return response.json();
	},

	/**
	 * List installed agent skills.
	 * @returns {Promise<{skills: object[]}>}
	 */
	async listSkills() {
		const response = await apiFetch("/api/skills");
		if (!response.ok) {
			throw new Error(
				await parseErrorDetail(response, "Failed to list skills"),
			);
		}
		return response.json();
	},

	/**
	 * Upload an agent skill as a .zip archive.
	 * The File is sent as the raw request body (no multipart).
	 * A 409 (skill id already exists) throws an Error carrying `status` 409 and
	 * `existing` (the existing skill summary) so the caller can offer to replace.
	 * @param {File|Blob} file - The .zip archive
	 * @param {{filename?: string, replace?: boolean}} [options]
	 * @returns {Promise<{skill: object, replaced: boolean}>}
	 */
	async uploadSkill(file, { filename, replace = false } = {}) {
		const params = new URLSearchParams();
		const resolvedFilename = filename || file?.name;
		if (resolvedFilename) {
			params.set("filename", resolvedFilename);
		}
		if (replace) {
			params.set("replace", "true");
		}
		const query = params.toString();
		const response = await apiFetch(`/api/skills${query ? `?${query}` : ""}`, {
			method: "POST",
			headers: {
				"Content-Type": "application/zip",
			},
			body: file,
		});

		if (response.status === 409) {
			const payload = await response.json().catch(() => null);
			const detail = payload?.detail;
			const error = new Error(
				typeof detail === "string" && detail.trim()
					? detail
					: "A skill with this name already exists",
			);
			error.status = 409;
			error.existing = payload?.existing || null;
			throw error;
		}

		if (!response.ok) {
			throw new Error(
				await parseErrorDetail(response, "Failed to upload skill"),
			);
		}
		return response.json();
	},

	/**
	 * Get the detail record for one skill.
	 * @param {string} skillId - The skill id
	 */
	async getSkill(skillId) {
		const response = await apiFetch(skillPath(skillId));
		if (!response.ok) {
			throw new Error(await parseErrorDetail(response, "Failed to get skill"));
		}
		return response.json();
	},

	/**
	 * Get the raw SKILL.md text for one skill.
	 * @param {string} skillId - The skill id
	 * @returns {Promise<{markdown: string}>}
	 */
	async getSkillMarkdown(skillId) {
		const response = await apiFetch(`${skillPath(skillId)}/markdown`);
		if (!response.ok) {
			throw new Error(
				await parseErrorDetail(response, "Failed to get skill markdown"),
			);
		}
		return response.json();
	},

	/**
	 * Enable or disable a skill.
	 * @param {string} skillId - The skill id
	 * @param {boolean} enabled - Whether the skill can be selected
	 */
	async setSkillEnabled(skillId, enabled) {
		const response = await apiFetch(skillPath(skillId), {
			method: "PATCH",
			headers: {
				"Content-Type": "application/json",
			},
			body: JSON.stringify({ enabled }),
		});
		if (!response.ok) {
			throw new Error(
				await parseErrorDetail(response, "Failed to update skill"),
			);
		}
		return response.json();
	},

	/**
	 * Delete a skill and its stored bundle.
	 * @param {string} skillId - The skill id
	 * @returns {Promise<{status: string, deleted: boolean}>}
	 */
	async deleteSkill(skillId) {
		const response = await apiFetch(skillPath(skillId), {
			method: "DELETE",
		});
		if (!response.ok) {
			throw new Error(
				await parseErrorDetail(response, "Failed to delete skill"),
			);
		}
		return response.json();
	},

	/**
	 * Send a message in a conversation.
	 * Returns immediately with consultation_id and status (started/queued).
	 * Use getConsultationStatus to poll for results.
	 * @param {string} conversationId - The conversation ID
	 * @param {string} content - The message text
	 * @param {string} [negativePrompt] - Optional negative-mode prompt
	 * @param {string[]} [skillIds] - Optional agent skill ids to apply
	 */
	async sendMessage(conversationId, content, negativePrompt, skillIds) {
		const response = await apiFetch(
			`${conversationPath(conversationId)}/message`,
			{
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({
					content,
					negative_prompt: negativePrompt || undefined,
					skill_ids:
						Array.isArray(skillIds) && skillIds.length ? skillIds : undefined,
				}),
			},
		);
		if (!response.ok) {
			throw new Error(
				await parseErrorDetail(response, "Failed to send message"),
			);
		}
		return response.json();
	},

	/**
	 * Open, resume, or create a side-chat thread for a specific council artifact.
	 */
	async openSideChat(conversationId, payload) {
		const response = await apiFetch(
			`${conversationPath(conversationId)}/side-chats/open`,
			{
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify(payload),
			},
		);
		if (!response.ok) {
			throw new Error(
				await parseErrorDetail(response, "Failed to open side chat"),
			);
		}
		return response.json();
	},

	/**
	 * Send a follow-up message in a persisted side-chat thread.
	 */
	async sendSideChatMessage(conversationId, threadId, messageIndex, content) {
		const response = await apiFetch(
			`${conversationPath(conversationId)}/side-chats/${pathSegment(threadId)}/message`,
			{
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({
					message_index: messageIndex,
					content,
				}),
			},
		);
		if (!response.ok) {
			throw new Error(
				await parseErrorDetail(response, "Failed to send side-chat message"),
			);
		}
		return response.json();
	},

	/**
	 * Submit a response to an interactive prompt.
	 * @param {string} sessionId - The interactive session ID
	 * @param {string} promptId - The prompt ID being responded to
	 * @param {object} responses - The user's responses
	 * @param {boolean} skipped - Whether the user skipped the prompt
	 */
	async submitInteractiveResponse(
		sessionId,
		promptId,
		responses,
		skipped = false,
	) {
		const response = await apiFetch("/api/interactive-response", {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
			},
			body: JSON.stringify({
				session_id: sessionId,
				prompt_id: promptId,
				responses,
				skipped,
			}),
		});

		if (!response.ok) {
			throw new Error("Failed to submit interactive response");
		}

		return response.json();
	},

	/**
	 * Re-run synthesis with different settings.
	 * Appends a new version and sets it as current.
	 * @param {string} conversationId - The conversation ID
	 * @param {number} messageIndex - The assistant message index (0-based)
	 * @param {string} synthesisMode - The synthesis mode (consensus, best_of_breed, comprehensive, weighted)
	 * @param {string} [focusAreas] - Optional focus areas for synthesis
	 * @returns {Promise<{status: string, version: object, version_index: number}>}
	 */
	async resynthesize(
		conversationId,
		messageIndex,
		synthesisMode,
		focusAreas = null,
	) {
		const response = await apiFetch(
			`${conversationPath(conversationId)}/resynthesize`,
			{
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({
					message_index: messageIndex,
					synthesis_mode: synthesisMode,
					focus_areas: focusAreas || undefined,
				}),
			},
		);

		if (!response.ok) {
			throw new Error(await parseErrorDetail(response, "Resynthesis failed"));
		}

		return response.json();
	},

	/**
	 * Export all context for one assistant response as markdown.
	 * @param {string} conversationId - The conversation ID
	 * @param {number} messageIndex - The assistant message index (0-based)
	 * @returns {Promise<{blob: Blob, filename: string}>}
	 */
	async exportMarkdown(conversationId, messageIndex) {
		const params = new URLSearchParams({ message_index: String(messageIndex) });
		const response = await apiFetch(
			`${conversationPath(conversationId)}/export-markdown?${params.toString()}`,
		);

		if (!response.ok) {
			throw new Error(await parseErrorDetail(response, "Export failed"));
		}

		const filename =
			parseFilenameFromContentDisposition(
				response.headers.get("Content-Disposition"),
			) || "llm-council-context.md";

		return {
			blob: await response.blob(),
			filename,
		};
	},

	/**
	 * Run synthesis for multiple modes in parallel.
	 * @param {string} conversationId - The conversation ID
	 * @param {number} messageIndex - The assistant message index (0-based)
	 * @param {string[]} [modes] - Optional specific modes to generate. If not provided, generates all missing modes.
	 * @returns {Promise<{status: string, versions: object[], version_indices: number[], errors?: object[]}>}
	 */
	async resynthesizeAll(conversationId, messageIndex, modes = null) {
		const response = await apiFetch(
			`${conversationPath(conversationId)}/resynthesize-all`,
			{
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({
					message_index: messageIndex,
					modes: modes || undefined,
				}),
			},
		);

		if (!response.ok) {
			throw new Error(await parseErrorDetail(response, "Resynthesis failed"));
		}

		return response.json();
	},

	/**
	 * Set a specific synthesis version as the current one.
	 * @param {string} conversationId - The conversation ID
	 * @param {number} messageIndex - The assistant message index (0-based)
	 * @param {number} versionIndex - The version index to set as current
	 * @returns {Promise<{status: string}>}
	 */
	async setCurrentVersion(conversationId, messageIndex, versionIndex) {
		const response = await apiFetch(
			`${conversationPath(conversationId)}/set-current-version`,
			{
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify({
					message_index: messageIndex,
					version_index: versionIndex,
				}),
			},
		);

		if (!response.ok) {
			throw new Error(
				await parseErrorDetail(response, "Failed to set current version"),
			);
		}

		return response.json();
	},

	/**
	 * Get consultation status for a conversation.
	 */
	async getConsultationStatus(conversationId) {
		const response = await apiFetch(
			`${conversationPath(conversationId)}/consultation/status`,
		);
		if (!response.ok) {
			throw new Error("Failed to get consultation status");
		}
		return response.json();
	},

	/**
	 * Get message queue for a conversation.
	 */
	async getConversationQueue(conversationId) {
		const response = await apiFetch(
			`${conversationPath(conversationId)}/queue`,
		);
		if (!response.ok) {
			throw new Error("Failed to get conversation queue");
		}
		return response.json();
	},

	/**
	 * Get global consultation system status.
	 */
	async getGlobalConsultationStatus() {
		const response = await apiFetch("/api/consultations/status");
		if (!response.ok) {
			throw new Error("Failed to get consultation status");
		}
		return response.json();
	},

	/**
	 * Cancel an in-progress consultation.
	 */
	async cancelConsultation(conversationId) {
		const response = await apiFetch(
			`${conversationPath(conversationId)}/consultation/cancel`,
			{
				method: "POST",
			},
		);
		if (!response.ok) {
			throw new Error("Failed to cancel consultation");
		}
		return response.json();
	},

	/**
	 * Clear any remembered auth failure so the next request retries the backend.
	 * Call this after the user submits a new API key.
	 */
	resetAuthState() {
		authFailure = null;
		authProbe = null;
	},
};
