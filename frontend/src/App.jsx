import { useEffect, useCallback, useMemo, useState } from "react";
import { produce } from "immer";
import Sidebar from "./components/Sidebar";
import ChatInterface from "./components/ChatInterface";
import ApiKeyModal from "./components/ApiKeyModal";
import SkillsModal from "./components/SkillsModal";
import { Sheet, SheetContent } from "./components/ui/sheet";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "./components/ui/alert-dialog";
import { api, API_AUTH_ERROR_EVENT } from "./api";
import { apiKeyStorage } from "./utils/apiKeyStorage";
import { skillSelectionStorage } from "./utils/skillSelectionStorage";
import { useNotifications } from "./hooks/useNotifications";
import {
	mergeConversation,
	ensureLoadingState,
} from "./utils/conversationMerge";
import { useAppStore } from "./store/useAppStore";

function findAssistantMessageIndexForStatus(messages, status) {
	if (!Array.isArray(messages) || messages.length === 0 || !status) {
		return -1;
	}

	if (
		Number.isInteger(status.assistant_index) &&
		messages[status.assistant_index]?.role === "assistant"
	) {
		return status.assistant_index;
	}

	const consultationId =
		status.consultation_id || status.metadata?.consultation_id;
	if (consultationId) {
		const matchingIndex = messages.findIndex(
			(msg) =>
				msg.role === "assistant" &&
				msg.metadata?.consultation_id === consultationId,
		);
		if (matchingIndex >= 0) {
			return matchingIndex;
		}
	}

	if (Number.isInteger(status.assistant_index) || consultationId) {
		return -1;
	}

	for (let index = messages.length - 1; index >= 0; index--) {
		if (messages[index]?.role === "assistant") {
			return index;
		}
	}

	return -1;
}

function findLatestAssistantStatusIndex(messages) {
	if (!Array.isArray(messages) || messages.length === 0) {
		return -1;
	}

	for (let index = messages.length - 1; index >= 0; index--) {
		const message = messages[index];
		if (message?.role !== "assistant") {
			continue;
		}
		if (message.stage3 || message.metadata?.status) {
			return index;
		}
	}

	return -1;
}

function isActiveConsultationStatus(status) {
	return Boolean(
		status && !["complete", "failed", "none"].includes(status.status),
	);
}

const EMPTY_SKILL_IDS = [];

// Drop unknown/duplicate ids so a deleted skill silently leaves the draft
// selection instead of failing validation on send.
function normalizeSkillIds(candidateIds, availableIds) {
	if (!Array.isArray(candidateIds)) {
		return [];
	}
	const seen = new Set();
	const normalized = [];
	for (const skillId of candidateIds) {
		if (typeof skillId !== "string") continue;
		if (seen.has(skillId) || !availableIds.has(skillId)) continue;
		seen.add(skillId);
		normalized.push(skillId);
	}
	return normalized;
}

function sameSkillIds(left, right) {
	if (left === right) return true;
	if (!Array.isArray(left) || !Array.isArray(right)) return false;
	if (left.length !== right.length) return false;
	return left.every((value, index) => value === right[index]);
}

function collectAvailableSkillIds(skills) {
	const availableIds = new Set();
	if (!Array.isArray(skills)) return availableIds;
	for (const skill of skills) {
		// Disabled skills are rejected by the backend on send, so treat them exactly
		// like deleted ones and let them drop out of the draft silently.
		if (skill?.id && skill.enabled !== false) availableIds.add(skill.id);
	}
	return availableIds;
}

// The read-only applied record on the most recent assistant message seeds the
// first-ever shared draft (and absorbs legacy per-chat drafts). Auto-selected
// runs store the picked skills under "resolved", so fall back to that when
// "requested" is empty.
function findLastRequestedSkillIds(conversations, conversationsById) {
	if (!Array.isArray(conversations)) return EMPTY_SKILL_IDS;
	for (let c = conversations.length - 1; c >= 0; c--) {
		const conversationId = conversations[c]?.id;
		const messages = conversationsById[conversationId]?.messages;
		if (!Array.isArray(messages)) continue;
		for (let m = messages.length - 1; m >= 0; m--) {
			const message = messages[m];
			if (message?.role !== "assistant") continue;
			const skillsMeta = message.metadata?.skills;
			const requested = skillsMeta?.requested;
			if (Array.isArray(requested) && requested.length) {
				return requested;
			}
			const resolved = skillsMeta?.resolved;
			if (Array.isArray(resolved)) {
				const ids = resolved
					.map((entry) => entry?.id)
					.filter((id) => typeof id === "string" && id);
				if (ids.length) return ids;
			}
		}
	}
	return EMPTY_SKILL_IDS;
}

function App() {
	const {
		conversations,
		setConversations,
		currentConversationId,
		conversationsById,
		isLoading,
		setCurrentConversationId,
		setConversationsById,
		setIsLoading,
		setError,
		config,
		setConfig,
		isSidebarOpen,
		setIsSidebarOpen,
		deleteTarget,
		setDeleteTarget,
		isClearConfirmOpen,
		setIsClearConfirmOpen,
		consultationStatus,
		setConsultationStatus,
		setMessageQueue,
		setBackendConsultations,
		setStreamingConsultations,
		interactiveSessionId,
		setInteractiveSessionId,
		currentInteractivePrompt,
		setCurrentInteractivePrompt,
		isCancelling,
		setIsCancelling,
		hasApiKey,
		setHasApiKey,
		showApiKeyModal,
		setShowApiKeyModal,
		apiKeyError,
		setApiKeyError,
		skills,
		setSkills,
		skillsLoaded,
		setSkillsLoaded,
		selectedSkillIds: rawSelectedSkillIds,
		setSelectedSkillIds,
		excludedSkillIdsByConversation,
		setExcludedSkillIdsByConversation,
		isSkillsModalOpen,
		setIsSkillsModalOpen,
	} = useAppStore();

	const [skillsModalFocusId, setSkillsModalFocusId] = useState(null);
	const [skillSources, setSkillSources] = useState({
		codex: false,
		claude: false,
	});

	// Notifications
	const { requestPermission, notifyInputNeeded } = useNotifications();

	// Handle API errors that require re-authentication
	const handleApiError = useCallback(
		(err) => {
			if (err.message === "NO_API_KEY" || err.message === "INVALID_API_KEY") {
				setApiKeyError(
					err.message === "INVALID_API_KEY"
						? "Your API key was invalid or has expired. Please enter a new one."
						: null,
				);
				setShowApiKeyModal(true);
				setHasApiKey(false);
				return true; // Error was handled
			}
			if (err.message === "INSECURE_CONTEXT") {
				setError({
					message: "Secure connection required",
					details:
						"API requests require HTTPS in production. Please use a secure connection.",
				});
				return true;
			}
			if (err.message === "API_KEY_RATE_LIMITED") {
				const retryAfterSeconds = Number.isFinite(err.retryAfterSeconds)
					? Math.ceil(err.retryAfterSeconds)
					: null;
				setError({
					message: "API key temporarily locked",
					details: retryAfterSeconds
						? `Too many invalid API key attempts. Try again in ${retryAfterSeconds} seconds.`
						: "Too many invalid API key attempts. Try again later.",
				});
				return true;
			}
			return false; // Error not handled
		},
		[setApiKeyError, setShowApiKeyModal, setHasApiKey, setError],
	);

	useEffect(() => {
		const handleGlobalApiError = (event) => {
			const message = event?.detail?.message;
			if (!message) return;

			const error = new Error(message);
			if (Number.isFinite(event.detail.retryAfterSeconds)) {
				error.retryAfterSeconds = event.detail.retryAfterSeconds;
			}
			handleApiError(error);
		};

		window.addEventListener(API_AUTH_ERROR_EVENT, handleGlobalApiError);
		return () =>
			window.removeEventListener(API_AUTH_ERROR_EVENT, handleGlobalApiError);
	}, [handleApiError]);

	const getConversationIdFromUrl = useCallback(() => {
		const params = new URLSearchParams(window.location.search);
		return params.get("conversation");
	}, []);

	const updateConversationUrl = useCallback(
		(conversationId, replace = false) => {
			const url = new URL(window.location.href);
			if (conversationId) {
				url.searchParams.set("conversation", conversationId);
			} else {
				url.searchParams.delete("conversation");
			}
			const method = replace ? "replaceState" : "pushState";
			window.history[method]({ conversationId }, "", url.toString());
		},
		[],
	);

	const selectConversation = useCallback(
		(conversationId, { updateUrl = true, replace = false } = {}) => {
			if (currentConversationId === conversationId) {
				if (updateUrl) {
					updateConversationUrl(conversationId, replace);
				}
				return;
			}
			setCurrentConversationId(conversationId);
			if (updateUrl) {
				updateConversationUrl(conversationId, replace);
			}
		},
		[currentConversationId, setCurrentConversationId, updateConversationUrl],
	);

	const updateConversationById = useCallback(
		(conversationId, updater) => {
			if (!conversationId) return;
			setConversationsById((prev) => {
				let base = prev[conversationId];
				if (!base) {
					const meta = useAppStore
						.getState()
						.conversations.find((conv) => conv.id === conversationId);
					base = {
						id: conversationId,
						created_at: meta?.created_at || new Date().toISOString(),
						title: meta?.title || "New Conversation",
						messages: [],
					};
				}
				const next = produce(base, updater);
				return {
					...prev,
					[conversationId]: next,
				};
			});
		},
		[setConversationsById],
	);

	const clearStreamingConsultation = useCallback(
		(conversationId) => {
			if (!conversationId) return;
			setStreamingConsultations((prev) => {
				if (!prev[conversationId]) return prev;
				const next = { ...prev };
				delete next[conversationId];
				return next;
			});
		},
		[setStreamingConsultations],
	);

	const applyConsultationStatusSnapshot = useCallback(
		(conversationId, status) => {
			if (!conversationId || !status || status.status === "none") return;

			updateConversationById(conversationId, (draft) => {
				if (!draft?.messages?.length) return;
				const targetIndex = findAssistantMessageIndexForStatus(
					draft.messages,
					status,
				);
				const lastMsg = targetIndex >= 0 ? draft.messages[targetIndex] : null;
				if (!lastMsg || lastMsg.role !== "assistant") return;

				if (status.stage1 !== undefined) lastMsg.stage1 = status.stage1;
				if (status.negative !== undefined) lastMsg.negative = status.negative;
				if (status.stage2 !== undefined) lastMsg.stage2 = status.stage2;
				if (status.stage3 !== undefined) lastMsg.stage3 = status.stage3;

				lastMsg.metadata = {
					...(lastMsg.metadata || {}),
					...(status.metadata || {}),
				};
				if (status.status === "failed") {
					lastMsg.metadata.status = "failed";
					if (status.error) {
						lastMsg.metadata.error = status.error;
					}
				} else if (status.status === "complete") {
					lastMsg.metadata.status = "complete";
				} else {
					lastMsg.metadata.status = status.status;
				}

				const loading = ensureLoadingState(lastMsg);
				loading.stage1 = status.status === "stage1";
				loading.negative = status.status === "negative";
				loading.stage2 = status.status === "stage2";
				loading.deliberation = status.status === "deliberation";
				loading.stage3 = status.status === "stage3";
			});
		},
		[updateConversationById],
	);

	const applyConversationSnapshot = useCallback(
		(conv) => {
			if (!conv?.id) return { status: null };

			let statusToReturn = null;
			let interactivePromptToReturn = null;
			const isCurrentConv =
				useAppStore.getState().currentConversationId === conv.id;

			setConversationsById((prev) => {
				let merged = mergeConversation(prev[conv.id], conv);
				const statusIndex = findLatestAssistantStatusIndex(merged?.messages);
				const statusMsgSnapshot =
					statusIndex >= 0 ? merged.messages[statusIndex] : null;
				const metadataStatus = statusMsgSnapshot?.metadata?.status || null;
				const status =
					metadataStatus === "failed"
						? "failed"
						: statusMsgSnapshot?.stage3
							? "complete"
							: metadataStatus;

				merged = produce(merged, (draft) => {
					const targetMsg =
						statusIndex >= 0 ? draft?.messages?.[statusIndex] : null;
					if (!targetMsg || targetMsg.role !== "assistant" || !status) return;
					const loading = ensureLoadingState(targetMsg);
					loading.stage1 = status === "stage1";
					loading.negative = status === "negative";
					loading.stage2 = status === "stage2";
					loading.deliberation = status === "deliberation";
					loading.stage3 = status === "stage3";
				});

				const interactivePrompt =
					status === "interactive"
						? statusMsgSnapshot?.metadata?.interactive_prompt || null
						: null;

				if (isCurrentConv) {
					statusToReturn = status;
					interactivePromptToReturn = interactivePrompt;
				}

				return {
					...prev,
					[conv.id]: merged,
				};
			});

			if (!isCurrentConv) {
				return { status: null };
			}

			if (statusToReturn === "interactive" && interactivePromptToReturn) {
				setCurrentInteractivePrompt(interactivePromptToReturn);
				if (interactivePromptToReturn.session_id) {
					setInteractiveSessionId(interactivePromptToReturn.session_id);
				}
			} else {
				setCurrentInteractivePrompt(null);
				setInteractiveSessionId(null);
			}

			if (
				statusToReturn &&
				statusToReturn !== "complete" &&
				statusToReturn !== "failed"
			) {
				setIsLoading(true);
				setStreamingConsultations((prev) => ({
					...prev,
					[conv.id]: statusToReturn,
				}));
			} else {
				setIsLoading(false);
				if (statusToReturn === "complete" || statusToReturn === "failed") {
					clearStreamingConsultation(conv.id);
				}
			}

			return {
				status: statusToReturn,
				interactivePrompt: interactivePromptToReturn,
			};
		},
		[
			clearStreamingConsultation,
			setCurrentInteractivePrompt,
			setInteractiveSessionId,
			setIsLoading,
			setStreamingConsultations,
			setConversationsById,
		],
	);

	const loadConversations = useCallback(async () => {
		try {
			const convs = await api.listConversations();
			setConversations(convs);
			if (convs.length > 0) {
				const prev = useAppStore.getState().conversationsById;
				let updated = null;
				convs.forEach((meta) => {
					if (prev[meta.id]) {
						const existing = prev[meta.id];
						if (
							existing.title !== meta.title ||
							existing.created_at !== meta.created_at
						) {
							updated = updated || { ...prev };
							updated[meta.id] = {
								...existing,
								title: meta.title,
								created_at: meta.created_at,
							};
						}
					}
				});
				if (updated) {
					setConversationsById((prev) => ({ ...prev, ...updated }));
				}
			}
		} catch (err) {
			console.error("Failed to load conversations:", err);
			if (!handleApiError(err)) {
				setError({
					message: "Failed to load conversations. Please refresh the page.",
					details: err.message,
				});
			}
		}
	}, [handleApiError, setConversations, setConversationsById, setError]);

	const loadConversation = useCallback(
		async (id) => {
			try {
				const conv = await api.getConversation(id);
				applyConversationSnapshot(conv);
			} catch (err) {
				console.error("Failed to load conversation:", err);
				if (!handleApiError(err)) {
					setError({
						message: "Failed to load conversation.",
						details: err.message,
					});
				}
			}
		},
		[applyConversationSnapshot, handleApiError, setError],
	);

	const loadConfig = useCallback(async () => {
		try {
			const cfg = await api.getConfig();
			setConfig(cfg);
		} catch (err) {
			console.error("Failed to load config:", err);
			if (!handleApiError(err)) {
				setConfig({
					negative_mode_enabled: false,
					deliberation_enabled: false,
					interactive_mode_enabled: false,
					skills_enabled: false,
					prompt_max_chars: null,
					negative_prompt_max_chars: null,
					synthesis_context_max_chars: null,
					council_models: [],
					chairman_model: null,
					title_model: null,
				});
			}
		}
	}, [handleApiError, setConfig]);

	const loadSkills = useCallback(async () => {
		try {
			const data = await api.listSkills();
			setSkills(Array.isArray(data?.skills) ? data.skills : []);
			setSkillSources(data?.sources || { codex: false, claude: false });
		} catch (err) {
			// Skills are disabled server-side (404) or unreachable; the picker
			// simply stays hidden instead of blocking the console.
			console.error("Failed to load skills:", err);
			handleApiError(err);
			setSkills([]);
		} finally {
			setSkillsLoaded(true);
		}
	}, [handleApiError, setSkills, setSkillsLoaded]);

	// Load conversations on mount (only if API key exists)
	useEffect(() => {
		if (hasApiKey) {
			loadConversations();
			loadConfig();
			loadSkills();
		}
	}, [hasApiKey, loadConfig, loadConversations, loadSkills]);

	const availableSkillIds = useMemo(
		() => collectAvailableSkillIds(skills),
		[skills],
	);

	// The GLOBAL DEFAULT skill selection: seeded once from localStorage - or from
	// the last applied assistant message when nothing was ever stored - and
	// always filtered against the installed skills. Per-chat toggles only ever
	// subtract from this default (see handleSkillIdsChange).
	useEffect(() => {
		if (!skillsLoaded || rawSelectedSkillIds !== null) return;

		const stored = skillSelectionStorage.getDefault();
		const source =
			stored !== null
				? stored
				: findLastRequestedSkillIds(conversations, conversationsById);

		const normalized = normalizeSkillIds(source, availableSkillIds);
		setSelectedSkillIds(normalized);
		skillSelectionStorage.setDefault(normalized);
	}, [
		availableSkillIds,
		conversations,
		conversationsById,
		rawSelectedSkillIds,
		setSelectedSkillIds,
		skillsLoaded,
	]);

	// A deleted or disabled skill silently drops out of the default.
	useEffect(() => {
		if (!skillsLoaded || rawSelectedSkillIds === null) return;
		const normalized = normalizeSkillIds(
			rawSelectedSkillIds,
			availableSkillIds,
		);
		if (sameSkillIds(rawSelectedSkillIds, normalized)) return;
		setSelectedSkillIds(normalized);
		skillSelectionStorage.setDefault(normalized);
	}, [
		availableSkillIds,
		rawSelectedSkillIds,
		setSelectedSkillIds,
		skillsLoaded,
	]);

	// The effective per-chat selection: the global default minus this chat's
	// toggled-off skills.
	const currentExclusions = new Set(
		(currentConversationId &&
			excludedSkillIdsByConversation[currentConversationId]) ||
			EMPTY_SKILL_IDS,
	);
	const selectedSkillIds = (rawSelectedSkillIds || EMPTY_SKILL_IDS).filter(
		(id) => !currentExclusions.has(id),
	);

	// Composer toggles write per-chat exclusions, never the default itself:
	// unchecking a default skill turns it off for this chat only.
	const handleSkillIdsChange = useCallback(
		(nextIds) => {
			if (!currentConversationId) return;
			const normalized = normalizeSkillIds(nextIds, availableSkillIds);
			const defaultIds = rawSelectedSkillIds || EMPTY_SKILL_IDS;
			const exclusions = defaultIds.filter((id) => !normalized.includes(id));
			setExcludedSkillIdsByConversation((prev) => ({
				...prev,
				[currentConversationId]: exclusions,
			}));
			skillSelectionStorage.setExclusions(currentConversationId, exclusions);
		},
		[
			availableSkillIds,
			currentConversationId,
			rawSelectedSkillIds,
			setExcludedSkillIdsByConversation,
		],
	);

	// The Skills modal edits the global default, capped at the backend's
	// per-consultation limit so an oversized default can never fail on send.
	const handleDefaultSkillIdsChange = useCallback(
		(nextIds) => {
			const normalized = normalizeSkillIds(nextIds, availableSkillIds);
			const cap = Number(config?.skills_max_selected);
			const capped = cap > 0 ? normalized.slice(0, cap) : normalized;
			setSelectedSkillIds(capped);
			skillSelectionStorage.setDefault(capped);
		},
		[availableSkillIds, config?.skills_max_selected, setSelectedSkillIds],
	);

	const handleResetSkillOverrides = useCallback(() => {
		if (!currentConversationId) return;
		setExcludedSkillIdsByConversation((prev) => {
			const next = { ...prev };
			delete next[currentConversationId];
			return next;
		});
		skillSelectionStorage.setExclusions(currentConversationId, []);
	}, [currentConversationId, setExcludedSkillIdsByConversation]);

	const handleOpenSkillsModal = useCallback(
		(focusSkillId) => {
			setSkillsModalFocusId(
				typeof focusSkillId === "string" ? focusSkillId : null,
			);
			setIsSkillsModalOpen(true);
		},
		[setIsSkillsModalOpen],
	);

	const handleCloseSkillsModal = useCallback(() => {
		setIsSkillsModalOpen(false);
		setSkillsModalFocusId(null);
	}, [setIsSkillsModalOpen]);

	// A 409 means the skill id is already installed: confirm, then retry with
	// replace=true. Returning null on a declined replace keeps the modal quiet.
	const handleUploadSkill = useCallback(
		async (file) => {
			try {
				const result = await api.uploadSkill(file);
				await loadSkills();
				return result;
			} catch (err) {
				if (err?.status === 409) {
					const existingId = err.existing?.id || "That skill";
					const existingVersion = err.existing?.version;
					const confirmed = window.confirm(
						`${existingId} is already installed${
							existingVersion ? ` (v${existingVersion})` : ""
						}. Replace all matching skills in the uploaded bundle?`,
					);
					if (!confirmed) {
						return null;
					}
					try {
						const replacedResult = await api.uploadSkill(file, {
							replace: true,
						});
						await loadSkills();
						return replacedResult;
					} catch (replaceErr) {
						handleApiError(replaceErr);
						throw replaceErr;
					}
				}
				handleApiError(err);
				throw err;
			}
		},
		[handleApiError, loadSkills],
	);

	const handleDeleteSkill = useCallback(
		async (skillId) => {
			try {
				const result = await api.deleteSkill(skillId);
				await loadSkills();
				return result;
			} catch (err) {
				handleApiError(err);
				throw err;
			}
		},
		[handleApiError, loadSkills],
	);

	const handleToggleSkillEnabled = useCallback(
		async (skillId, enabled) => {
			try {
				const result = await api.setSkillEnabled(skillId, enabled);
				await loadSkills();
				return result;
			} catch (err) {
				handleApiError(err);
				throw err;
			}
		},
		[handleApiError, loadSkills],
	);

	useEffect(() => {
		if (!hasApiKey) return;
		const conversationId = getConversationIdFromUrl();
		if (conversationId) {
			selectConversation(conversationId, { updateUrl: false, replace: true });
		}
	}, [getConversationIdFromUrl, hasApiKey, selectConversation]);

	useEffect(() => {
		if (!hasApiKey) return;
		const handlePopState = () => {
			const conversationId = getConversationIdFromUrl();
			selectConversation(conversationId, { updateUrl: false, replace: true });
		};
		window.addEventListener("popstate", handlePopState);
		return () => window.removeEventListener("popstate", handlePopState);
	}, [getConversationIdFromUrl, hasApiKey, selectConversation]);

	// Load conversation details when selected
	useEffect(() => {
		if (currentConversationId) {
			loadConversation(currentConversationId);
		}
	}, [currentConversationId, loadConversation]);

	useEffect(() => {
		setCurrentInteractivePrompt(null);
		setInteractiveSessionId(null);
	}, [
		currentConversationId,
		setCurrentInteractivePrompt,
		setInteractiveSessionId,
	]);

	// Gate the status loop on a boolean so per-snapshot state updates don't tear
	// down and restart the long-poll request.
	const isConsultationActive = isActiveConsultationStatus(consultationStatus);

	// Long-poll for consultation status. Each response is a full snapshot (status
	// plus queue) with a change cursor; the server holds the request until the
	// snapshot differs from the cursor we last saw, so updates arrive near-instantly
	// while idle waits cost one request per wait window. A dropped request loses
	// nothing - the next poll returns the complete current state.
	useEffect(() => {
		if (!hasApiKey || !currentConversationId || !isConsultationActive) {
			return;
		}

		const controller = new AbortController();
		let stopped = false;
		let cursor = null;

		const applySnapshot = async (status) => {
			const queuedMessages = status.queue || [];
			setMessageQueue(queuedMessages);

			if (status.status === "none") {
				setIsLoading(false);
				setConsultationStatus(null);
				clearStreamingConsultation(currentConversationId);
			} else {
				if (Number.isInteger(status.assistant_index)) {
					const localConversation =
						useAppStore.getState().conversationsById[currentConversationId];
					if (
						localConversation?.messages?.[status.assistant_index]?.role !==
						"assistant"
					) {
						await loadConversation(currentConversationId);
					}
				}

				setConsultationStatus(status);
				applyConsultationStatusSnapshot(currentConversationId, status);

				// Update conversation with partial results
				updateConversationById(currentConversationId, (draft) => {
					if (!draft || !draft.messages || draft.messages.length === 0) return;
					const targetIndex = findAssistantMessageIndexForStatus(
						draft.messages,
						status,
					);
					const lastMsg = targetIndex >= 0 ? draft.messages[targetIndex] : null;
					if (!lastMsg || lastMsg.role !== "assistant") return;

					if (status.stage1) lastMsg.stage1 = status.stage1;
					if (status.negative) lastMsg.negative = status.negative;
					if (status.stage2) {
						lastMsg.stage2 = status.stage2;
					}
					if (status.stage3) lastMsg.stage3 = status.stage3;
					if (status.metadata) {
						lastMsg.metadata = {
							...(lastMsg.metadata || {}),
							...status.metadata,
						};

						const interactivePrompt = status.metadata.interactive_prompt;
						if (interactivePrompt) {
							if (interactivePrompt.session_id) {
								setInteractiveSessionId(interactivePrompt.session_id);
							}
							setCurrentInteractivePrompt(interactivePrompt);
						} else if (interactivePrompt === null) {
							setCurrentInteractivePrompt(null);
							setInteractiveSessionId(null);
						}
					}

					const loading = ensureLoadingState(lastMsg);
					loading.stage1 = status.status === "stage1";
					loading.negative = status.status === "negative";
					loading.stage2 = status.status === "stage2";
					loading.deliberation = status.status === "deliberation";
					loading.stage3 = status.status === "stage3";
				});

				// Handle completion
				if (status.status === "complete") {
					clearStreamingConsultation(currentConversationId);
					loadConversations(); // Refresh list for title update
					if (queuedMessages.length > 0) {
						setIsLoading(true);
						setConsultationStatus({
							status: "pending",
							consultation_id: queuedMessages[0].id,
						});
					} else {
						setIsLoading(false);
						setConsultationStatus(null);
					}
				} else if (status.status === "failed") {
					clearStreamingConsultation(currentConversationId);
					if (status.error !== "Cancelled") {
						setError({
							message: "Consultation failed",
							details: status.error,
						});
					}
					if (queuedMessages.length > 0) {
						setIsLoading(true);
						setConsultationStatus({
							status: "pending",
							consultation_id: queuedMessages[0].id,
						});
					} else {
						setIsLoading(false);
						setConsultationStatus(null);
					}
				}
			}
		};

		const runLoop = async () => {
			while (!stopped) {
				try {
					const status = await api.getConsultationStatus(
						currentConversationId,
						{ wait: 25, cursor, signal: controller.signal },
					);
					if (stopped) break;
					cursor = status.cursor || null;
					await applySnapshot(status);
				} catch (err) {
					if (stopped || err?.name === "AbortError") break;
					console.error("Failed to poll consultation status:", err);
					handleApiError(err);
					// Back off before retrying so transient failures (server restart,
					// network blip, auth latch) don't spin the loop.
					await new Promise((resolve) => setTimeout(resolve, 2000));
				}
			}
		};

		runLoop();

		return () => {
			stopped = true;
			controller.abort();
		};
	}, [
		applyConsultationStatusSnapshot,
		clearStreamingConsultation,
		isConsultationActive,
		currentConversationId,
		handleApiError,
		hasApiKey,
		loadConversation,
		loadConversations,
		setConsultationStatus,
		setCurrentInteractivePrompt,
		setError,
		setInteractiveSessionId,
		setIsLoading,
		setMessageQueue,
		updateConversationById,
	]);

	// Check for active consultation when switching conversations
	useEffect(() => {
		if (!hasApiKey || !currentConversationId) {
			setConsultationStatus(null);
			setMessageQueue([]);
			setIsLoading(false);
			setStreamingConsultations({});
			return;
		}

		const checkConsultation = async () => {
			try {
				const status = await api.getConsultationStatus(currentConversationId);
				if (status.status === "none") {
					setConsultationStatus(null);
					setIsLoading(false);
					clearStreamingConsultation(currentConversationId);
				} else if (status.status === "complete" || status.status === "failed") {
					applyConsultationStatusSnapshot(currentConversationId, status);
					setConsultationStatus(null);
					setIsLoading(false);
					clearStreamingConsultation(currentConversationId);
					if (status.status === "failed" && status.error !== "Cancelled") {
						setError({ message: "Consultation failed", details: status.error });
					}
				} else {
					applyConsultationStatusSnapshot(currentConversationId, status);
					setConsultationStatus(status);
					setIsLoading(true);
				}

				setMessageQueue(status.queue || []);
			} catch (err) {
				console.error("Failed to check consultation status:", err);
				handleApiError(err);
			}
		};

		checkConsultation();
	}, [
		applyConsultationStatusSnapshot,
		clearStreamingConsultation,
		currentConversationId,
		handleApiError,
		hasApiKey,
		setConsultationStatus,
		setError,
		setIsLoading,
		setMessageQueue,
		setStreamingConsultations,
	]);

	// Long-poll for global consultation status (for sidebar indicators)
	useEffect(() => {
		if (!hasApiKey) return;

		const controller = new AbortController();
		let stopped = false;
		let cursor = null;

		const runLoop = async () => {
			while (!stopped) {
				try {
					const status = await api.getGlobalConsultationStatus({
						wait: 25,
						cursor,
						signal: controller.signal,
					});
					if (stopped) break;
					cursor = status.cursor || null;
					const consultations = {};
					for (const active of status.active) {
						consultations[active.conversation_id] = active.status;
					}
					setBackendConsultations(consultations);
				} catch (err) {
					if (stopped || err?.name === "AbortError") break;
					console.error("Failed to fetch global consultation status:", err);
					handleApiError(err);
					await new Promise((resolve) => setTimeout(resolve, 5000));
				}
			}
		};

		runLoop();

		return () => {
			stopped = true;
			controller.abort();
		};
	}, [handleApiError, hasApiKey, setBackendConsultations]);

	// Notify when interactive prompt arrives
	useEffect(() => {
		if (currentInteractivePrompt) {
			notifyInputNeeded();
		}
	}, [currentInteractivePrompt, notifyInputNeeded]);

	useEffect(() => {
		if (!hasApiKey) return;
		const syncCurrentConversation = async () => {
			const conversationId = useAppStore.getState().currentConversationId;
			if (!conversationId) return;
			try {
				const conv = await api.getConversation(conversationId);
				applyConversationSnapshot(conv);
			} catch (err) {
				console.error("Failed to sync conversation state:", err);
				handleApiError(err);
			}
		};

		const handleVisibility = () => {
			if (!document.hidden) {
				syncCurrentConversation();
			}
		};

		window.addEventListener("focus", syncCurrentConversation);
		window.addEventListener("online", syncCurrentConversation);
		document.addEventListener("visibilitychange", handleVisibility);

		return () => {
			window.removeEventListener("focus", syncCurrentConversation);
			window.removeEventListener("online", syncCurrentConversation);
			document.removeEventListener("visibilitychange", handleVisibility);
		};
	}, [applyConversationSnapshot, hasApiKey, handleApiError]);

	const handleInteractiveSubmit = async (responses) => {
		if (!interactiveSessionId || !currentInteractivePrompt) return;

		try {
			await api.submitInteractiveResponse(
				interactiveSessionId,
				currentInteractivePrompt.prompt_id,
				responses,
				false,
			);
			setCurrentInteractivePrompt(null);
			setInteractiveSessionId(null);
		} catch (err) {
			console.error("Failed to submit interactive response:", err);
			if (!handleApiError(err)) {
				setError({
					message: "Failed to submit response.",
					details: err.message,
				});
			}
		}
	};

	const handleInteractiveSkip = async () => {
		if (!interactiveSessionId || !currentInteractivePrompt) return;

		try {
			await api.submitInteractiveResponse(
				interactiveSessionId,
				currentInteractivePrompt.prompt_id,
				{},
				true,
			);
			setCurrentInteractivePrompt(null);
			setInteractiveSessionId(null);
		} catch (err) {
			console.error("Failed to skip interactive prompt:", err);
			if (!handleApiError(err)) {
				setError({ message: "Failed to skip prompt.", details: err.message });
			}
		}
	};

	const handleVersionAdded = useCallback(
		(messageIndex, version, options = {}) => {
			if (!currentConversationId) return;
			const { setAsCurrent = true } = options;
			// Append a new synthesis version to the stage3 versions array
			updateConversationById(currentConversationId, (draft) => {
				if (!draft?.messages?.[messageIndex]) return;
				const msg = draft.messages[messageIndex];
				if (!msg.stage3) return;

				// Ensure stage3 is in new format
				if (!msg.stage3.versions) {
					// Migrate old format
					const oldVersion = {
						model: msg.stage3.model,
						response: msg.stage3.response,
						mode: msg.stage3.mode || "best_of_breed",
						timestamp: new Date().toISOString(),
					};
					msg.stage3 = {
						current: oldVersion,
						versions: [oldVersion],
					};
				}

				// Append new version
				msg.stage3.versions.push(version);
				if (setAsCurrent) {
					msg.stage3.current = version;

					// Update synthesis mode in metadata
					if (version.mode) {
						if (!msg.metadata) {
							msg.metadata = {};
						}
						msg.metadata.synthesis_mode = version.mode;
					}
				}
			});
		},
		[currentConversationId, updateConversationById],
	);

	const handleCurrentChanged = useCallback(
		(messageIndex, versionIndex) => {
			if (!currentConversationId) return;
			// Set a different version as the current one
			updateConversationById(currentConversationId, (draft) => {
				if (!draft?.messages?.[messageIndex]) return;
				const msg = draft.messages[messageIndex];
				if (!msg.stage3?.versions?.[versionIndex]) return;

				msg.stage3.current = msg.stage3.versions[versionIndex];

				// Update synthesis mode in metadata
				const mode = msg.stage3.current.mode;
				if (mode) {
					if (!msg.metadata) {
						msg.metadata = {};
					}
					msg.metadata.synthesis_mode = mode;
				}
			});
		},
		[currentConversationId, updateConversationById],
	);

	const handleNewConversation = async () => {
		setError(null);

		// Check if current conversation is empty and should be auto-deleted
		let conversationToDelete = null;
		if (currentConversationId) {
			const currentConv = conversations.find(
				(c) => c.id === currentConversationId,
			);
			if (currentConv && currentConv.message_count === 0) {
				conversationToDelete = currentConversationId;
			}
		}

		try {
			// Delete empty conversation if needed
			if (conversationToDelete) {
				await api.deleteConversation(conversationToDelete);
				if (useAppStore.getState().conversationsById[conversationToDelete]) {
					setConversationsById((prev) => {
						const next = { ...prev };
						delete next[conversationToDelete];
						return next;
					});
				}
			}

			const newConv = await api.createConversation();
			setConversations((prev) => [
				{
					id: newConv.id,
					created_at: newConv.created_at,
					title: newConv.title,
					message_count: 0,
				},
				...prev.filter((c) => c.id !== conversationToDelete),
			]);
			setConversationsById((prev) => ({
				...prev,
				[newConv.id]: {
					id: newConv.id,
					created_at: newConv.created_at,
					title: newConv.title,
					messages: [],
				},
			}));
			selectConversation(newConv.id);
		} catch (err) {
			console.error("Failed to create conversation:", err);
			if (!handleApiError(err)) {
				setError({
					message: "Failed to create new conversation.",
					details: err.message,
				});
			}
		}
	};

	const handleDeleteConversation = (conversationId) => {
		const conversationTitle =
			conversations.find((conv) => conv.id === conversationId)?.title ||
			"New Consultation";
		setDeleteTarget({ id: conversationId, title: conversationTitle });
	};

	const confirmDeleteConversation = async () => {
		if (!deleteTarget) return;

		setError(null);
		try {
			await api.deleteConversation(deleteTarget.id);
			const remaining = conversations.filter(
				(conv) => conv.id !== deleteTarget.id,
			);
			setConversations(remaining);
			if (useAppStore.getState().conversationsById[deleteTarget.id]) {
				setConversationsById((prev) => {
					const next = { ...prev };
					delete next[deleteTarget.id];
					return next;
				});
			}
			if (currentConversationId === deleteTarget.id) {
				selectConversation(null, { replace: true });
			}
			setDeleteTarget(null);
		} catch (err) {
			console.error("Failed to delete conversation:", err);
			if (!handleApiError(err)) {
				setError({
					message: "Failed to delete conversation.",
					details: err.message,
				});
			}
		}
	};

	const handleClearConversations = () => {
		if (conversations.length === 0) return;

		setIsClearConfirmOpen(true);
	};

	const confirmClearConversations = async () => {
		setError(null);
		try {
			await api.clearConversations();
			setConversations([]);
			selectConversation(null, { replace: true });
			setConversationsById({});
			setStreamingConsultations({});
			setIsClearConfirmOpen(false);
		} catch (err) {
			console.error("Failed to delete conversations:", err);
			if (!handleApiError(err)) {
				setError({
					message: "Failed to delete conversations.",
					details: err.message,
				});
			}
		}
	};

	// API key handlers
	const handleApiKeySubmit = (key) => {
		apiKeyStorage.set(key);
		api.resetAuthState();
		setHasApiKey(true);
		setShowApiKeyModal(false);
		setApiKeyError(null);
	};

	const handleApiKeyClear = useCallback(() => {
		apiKeyStorage.clear();
		setHasApiKey(false);
		setShowApiKeyModal(true);
		setConversations([]);
		selectConversation(null, { replace: true });
		setConversationsById({});
		setStreamingConsultations({});
		setConfig(null);
	}, [
		selectConversation,
		setConfig,
		setConversations,
		setConversationsById,
		setHasApiKey,
		setShowApiKeyModal,
		setStreamingConsultations,
	]);

	const handleCancelConsultation = useCallback(async () => {
		if (!currentConversationId) return;
		if (isCancelling) return;

		const conversationId = currentConversationId;
		setIsCancelling(true);
		setError(null);

		try {
			await api.cancelConsultation(conversationId);
			setIsLoading(false);
			setConsultationStatus(null);
			clearStreamingConsultation(conversationId);
			setCurrentInteractivePrompt(null);
			setInteractiveSessionId(null);
			setMessageQueue([]);
			await loadConversation(conversationId);
			await loadConversations();
		} catch (err) {
			console.error("Failed to cancel consultation:", err);
			if (!handleApiError(err)) {
				setError({
					message: "Failed to cancel consultation.",
					details: err.message,
				});
			}
		} finally {
			setIsCancelling(false);
		}
	}, [
		currentConversationId,
		clearStreamingConsultation,
		handleApiError,
		isCancelling,
		loadConversations,
		loadConversation,
		setConsultationStatus,
		setCurrentInteractivePrompt,
		setError,
		setInteractiveSessionId,
		setIsCancelling,
		setIsLoading,
		setMessageQueue,
	]);

	const handleSelectConversation = (id) => {
		selectConversation(id);
	};

	const handleSendMessage = async (content, negativePrompt, skillIds) => {
		if (!currentConversationId) return;

		// Request notification permission on first message
		requestPermission();

		setError(null);
		setCurrentInteractivePrompt(null);
		setInteractiveSessionId(null);

		try {
			const conversationId = currentConversationId;
			const requestedSkillIds = normalizeSkillIds(
				skillIds === undefined ? selectedSkillIds : skillIds,
				availableSkillIds,
			);
			const userMessage = { role: "user", content };
			const assistantMessage = {
				role: "assistant",
				stage1: null,
				negative: null,
				stage2: null,
				stage3: null,
				metadata: null,
				loading: {
					stage1: false,
					negative: false,
					stage2: false,
					deliberation: false,
					stage3: false,
				},
			};

			const rollbackOptimisticMessagePair = () => {
				updateConversationById(conversationId, (draft) => {
					if (!draft || !draft.messages || draft.messages.length === 0) return;
					const lastMsg = draft.messages[draft.messages.length - 1];
					if (!lastMsg || lastMsg.role !== "assistant") return;
					const isEmpty =
						!lastMsg.stage1 &&
						!lastMsg.negative &&
						!lastMsg.stage2 &&
						!lastMsg.stage3 &&
						!lastMsg.metadata;
					if (isEmpty) {
						draft.messages.pop();
					}
					const previousMsg = draft.messages[draft.messages.length - 1];
					if (previousMsg?.role === "user" && previousMsg.content === content) {
						draft.messages.pop();
					}
				});
				setConversations((prev) =>
					prev.map((c) =>
						c.id === conversationId
							? { ...c, message_count: Math.max(0, (c.message_count || 0) - 2) }
							: c,
					),
				);
			};

			updateConversationById(conversationId, (draft) => {
				if (!draft.messages) {
					draft.messages = [];
				}
				draft.messages.push(userMessage);
				draft.messages.push(assistantMessage);
			});

			setConversations((prev) =>
				prev.map((c) =>
					c.id === conversationId
						? { ...c, message_count: (c.message_count || 0) + 2 }
						: c,
				),
			);

			const activeBeforeSend = isActiveConsultationStatus(consultationStatus)
				? consultationStatus
				: null;
			setIsLoading(true);
			if (!activeBeforeSend) {
				setConsultationStatus(null);
			}

			try {
				const result = await api.sendMessage(
					conversationId,
					content,
					negativePrompt,
					requestedSkillIds,
				);

				if (result.status === "queued") {
					rollbackOptimisticMessagePair();
					setMessageQueue((prev) => [
						...prev,
						{
							id: result.consultation_id,
							content,
							created_at: new Date().toISOString(),
						},
					]);
					setConsultationStatus(activeBeforeSend);
					setIsLoading(isLoading || Boolean(activeBeforeSend));
					return;
				}

				setIsLoading(true);
				updateConversationById(conversationId, (draft) => {
					const lastMsg = draft?.messages?.[draft.messages.length - 1];
					if (!lastMsg || lastMsg.role !== "assistant") return;
					lastMsg.metadata = {
						...(lastMsg.metadata || {}),
						consultation_id: result.consultation_id,
						status: "pending",
					};
				});
				setConsultationStatus({
					status: "pending",
					consultation_id: result.consultation_id,
				});
			} catch (err) {
				setIsLoading(false);
				rollbackOptimisticMessagePair();
				if (!handleApiError(err)) {
					setError({
						message: "Failed to send message.",
						details: err.message,
					});
				}
			}
		} catch (err) {
			console.error("Failed to prepare message send:", err);
			if (!handleApiError(err)) {
				setError({
					message: "Failed to prepare message.",
					details: err.message,
				});
			}
		}
	};

	return (
		<>
			<a href="#main-content" className="skip-link">
				Skip to content
			</a>
			<ApiKeyModal
				isOpen={showApiKeyModal}
				onSubmit={handleApiKeySubmit}
				error={apiKeyError}
			/>
			<AlertDialog
				open={Boolean(deleteTarget)}
				onOpenChange={(open) => {
					if (!open) setDeleteTarget(null);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete consultation?</AlertDialogTitle>
						<AlertDialogDescription>
							Delete "{deleteTarget?.title || "New Consultation"}"? This cannot
							be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={confirmDeleteConversation}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
			<AlertDialog
				open={isClearConfirmOpen}
				onOpenChange={(open) => {
					if (!open) setIsClearConfirmOpen(false);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete all consultations?</AlertDialogTitle>
						<AlertDialogDescription>
							This will remove every saved consultation and cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={confirmClearConversations}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete All
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
			<SkillsModal
				isOpen={isSkillsModalOpen}
				onClose={handleCloseSkillsModal}
				skills={skills}
				sources={skillSources}
				onSourcesChange={async (sources) => {
					await api.setSkillSources(sources);
					await loadSkills();
				}}
				onUpload={handleUploadSkill}
				onDelete={handleDeleteSkill}
				onToggleEnabled={handleToggleSkillEnabled}
				focusSkillId={skillsModalFocusId}
				defaultSkillIds={rawSelectedSkillIds || EMPTY_SKILL_IDS}
				onDefaultSkillIdsChange={handleDefaultSkillIdsChange}
			/>
			<Sheet open={isSidebarOpen} onOpenChange={setIsSidebarOpen}>
				<SheetContent side="left" className="p-0">
					<Sidebar
						onSelectConversation={handleSelectConversation}
						onNewConversation={handleNewConversation}
						onDeleteConversation={handleDeleteConversation}
						onClearConversations={handleClearConversations}
						onClose={() => setIsSidebarOpen(false)}
						className="h-full rounded-none border-0 shadow-none"
					/>
				</SheetContent>
			</Sheet>
			<div className="flex min-h-[100dvh] w-full">
				<aside
					className="app-sidebar hidden w-[320px] shrink-0 p-4 lg:block"
					aria-label="Consultations sidebar"
				>
					<Sidebar
						onSelectConversation={handleSelectConversation}
						onNewConversation={handleNewConversation}
						onDeleteConversation={handleDeleteConversation}
						onClearConversations={handleClearConversations}
						className="h-full"
					/>
				</aside>
				<main
					id="main-content"
					tabIndex={-1}
					className="app-main flex min-w-0 flex-1 flex-col lg:p-4"
					aria-label="Consultation content"
				>
					<div className="flex min-h-[100dvh] min-w-0 flex-1 flex-col overflow-hidden lg:min-h-0 lg:rounded-xl lg:glass">
						<ChatInterface
							onSendMessage={handleSendMessage}
							onInteractiveSubmit={handleInteractiveSubmit}
							onInteractiveSkip={handleInteractiveSkip}
							onApiKeyClear={handleApiKeyClear}
							onOpenSidebar={() => setIsSidebarOpen(true)}
							onCancelConsultation={handleCancelConsultation}
							onVersionAdded={handleVersionAdded}
							onCurrentChanged={handleCurrentChanged}
							selectedSkillIds={selectedSkillIds}
							defaultSkillIds={rawSelectedSkillIds || EMPTY_SKILL_IDS}
							onSkillIdsChange={handleSkillIdsChange}
							onResetSkillOverrides={handleResetSkillOverrides}
							onOpenSkillsModal={handleOpenSkillsModal}
						/>
					</div>
				</main>
			</div>
		</>
	);
}

export default App;
