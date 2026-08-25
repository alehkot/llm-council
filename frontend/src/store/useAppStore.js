import { create } from "zustand";
import { apiKeyStorage } from "../utils/apiKeyStorage";

const DEFAULT_STREAM_TIMEOUT_MS = 10 * 60 * 1000;

const getInitialApiKeyState = () => {
	if (typeof window === "undefined") return false;
	try {
		return !!apiKeyStorage.get();
	} catch {
		return false;
	}
};

const applyUpdate = (current, next) =>
	typeof next === "function" ? next(current) : next;

const initialHasApiKey = getInitialApiKeyState();

export const useAppStore = create((set) => ({
	conversations: [],
	currentConversationId: null,
	conversationsById: {},
	isLoading: false,
	error: null,
	config: null,
	streamTimeoutMs: DEFAULT_STREAM_TIMEOUT_MS,
	isSidebarOpen: false,
	deleteTarget: null,
	isClearConfirmOpen: false,
	consultationStatus: null,
	messageQueue: [],
	backendConsultations: {},
	streamingConsultations: {},
	interactiveSessionId: null,
	currentInteractivePrompt: null,
	isCancelling: false,
	hasApiKey: initialHasApiKey,
	showApiKeyModal: !initialHasApiKey,
	apiKeyError: null,
	skills: [],
	skillsLoaded: false,
	selectedSkillIds: null,
	excludedSkillIdsByConversation: {},
	isSkillsModalOpen: false,

	setConversations: (next) =>
		set((state) => ({
			conversations: applyUpdate(state.conversations, next),
		})),
	setCurrentConversationId: (next) =>
		set((state) => ({
			currentConversationId: applyUpdate(state.currentConversationId, next),
		})),
	setConversationsById: (next) =>
		set((state) => ({
			conversationsById: applyUpdate(state.conversationsById, next),
		})),
	setIsLoading: (next) =>
		set((state) => ({
			isLoading: applyUpdate(state.isLoading, next),
		})),
	setError: (next) =>
		set((state) => ({
			error: applyUpdate(state.error, next),
		})),
	setConfig: (next) =>
		set((state) => ({
			config: applyUpdate(state.config, next),
		})),
	setStreamTimeoutMs: (next) =>
		set((state) => ({
			streamTimeoutMs: applyUpdate(state.streamTimeoutMs, next),
		})),
	setIsSidebarOpen: (next) =>
		set((state) => ({
			isSidebarOpen: applyUpdate(state.isSidebarOpen, next),
		})),
	setDeleteTarget: (next) =>
		set((state) => ({
			deleteTarget: applyUpdate(state.deleteTarget, next),
		})),
	setIsClearConfirmOpen: (next) =>
		set((state) => ({
			isClearConfirmOpen: applyUpdate(state.isClearConfirmOpen, next),
		})),
	setConsultationStatus: (next) =>
		set((state) => ({
			consultationStatus: applyUpdate(state.consultationStatus, next),
		})),
	setMessageQueue: (next) =>
		set((state) => ({
			messageQueue: applyUpdate(state.messageQueue, next),
		})),
	setBackendConsultations: (next) =>
		set((state) => ({
			backendConsultations: applyUpdate(state.backendConsultations, next),
		})),
	setStreamingConsultations: (next) =>
		set((state) => ({
			streamingConsultations: applyUpdate(state.streamingConsultations, next),
		})),
	setInteractiveSessionId: (next) =>
		set((state) => ({
			interactiveSessionId: applyUpdate(state.interactiveSessionId, next),
		})),
	setCurrentInteractivePrompt: (next) =>
		set((state) => ({
			currentInteractivePrompt: applyUpdate(
				state.currentInteractivePrompt,
				next,
			),
		})),
	setIsCancelling: (next) =>
		set((state) => ({
			isCancelling: applyUpdate(state.isCancelling, next),
		})),
	setHasApiKey: (next) =>
		set((state) => ({
			hasApiKey: applyUpdate(state.hasApiKey, next),
		})),
	setShowApiKeyModal: (next) =>
		set((state) => ({
			showApiKeyModal: applyUpdate(state.showApiKeyModal, next),
		})),
	setApiKeyError: (next) =>
		set((state) => ({
			apiKeyError: applyUpdate(state.apiKeyError, next),
		})),
	setSkills: (next) =>
		set((state) => ({
			skills: applyUpdate(state.skills, next),
		})),
	setSkillsLoaded: (next) =>
		set((state) => ({
			skillsLoaded: applyUpdate(state.skillsLoaded, next),
		})),
	setSelectedSkillIds: (next) =>
		set((state) => ({
			selectedSkillIds: applyUpdate(state.selectedSkillIds, next),
		})),
	setExcludedSkillIdsByConversation: (next) =>
		set((state) => ({
			excludedSkillIdsByConversation: applyUpdate(
				state.excludedSkillIdsByConversation,
				next,
			),
		})),
	setIsSkillsModalOpen: (next) =>
		set((state) => ({
			isSkillsModalOpen: applyUpdate(state.isSkillsModalOpen, next),
		})),
}));
