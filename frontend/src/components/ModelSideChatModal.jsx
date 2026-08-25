import { useCallback, useEffect, useState } from "react";
import { produce } from "immer";
import { api } from "../api";
import Markdown from "./Markdown";
import { useAppStore } from "../store/useAppStore";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "./ui/dialog";
import { Label } from "./ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "./ui/select";
import { Textarea } from "./ui/textarea";

function formatModelName(model) {
	if (!model) return "unknown";
	return model.includes("/") ? model.split("/")[1] : model;
}

function formatTimestamp(value) {
	if (!value) return "";
	try {
		return new Date(value).toLocaleString([], {
			month: "short",
			day: "numeric",
			hour: "numeric",
			minute: "2-digit",
		});
	} catch {
		return "";
	}
}

function threadMatchesSource(thread, source, selectedModel) {
	if (!thread || !source) return false;
	if (thread.target_model !== selectedModel) return false;
	if (thread.source_type !== source.sourceType) return false;

	if (source.sourceType === "stage3") {
		if (source.sourceKey && thread.source_key) {
			return thread.source_key === source.sourceKey;
		}
		if (
			source.sourceMode &&
			thread.source_mode &&
			thread.source_mode !== source.sourceMode
		) {
			return false;
		}
		return thread.source_version_index === source.versionIndex;
	}

	return thread.source_model === source.sourceModel;
}

function buildThreadLabel(thread, index) {
	const messageCount = Array.isArray(thread?.messages)
		? thread.messages.length
		: 0;
	const updatedAt = formatTimestamp(thread?.updated_at || thread?.created_at);
	return `Thread ${index + 1} · ${messageCount} msgs${updatedAt ? ` · ${updatedAt}` : ""}`;
}

function getFallbackSourceNote(sourceType) {
	if (sourceType === "stage1") {
		return "Original individual response before peer review or synthesis.";
	}
	if (sourceType === "deliberation") {
		return "Deliberation-round revision after the model reviewed peer feedback.";
	}
	if (sourceType === "stage3") {
		return "Synthesis snapshot from the chairman stage.";
	}
	return "";
}

export default function ModelSideChatModal({
	isOpen,
	onOpenChange,
	conversationId,
	assistantAbsoluteIndex,
	assistantMessageIndex,
	assistantMessage,
	source,
	availableModels,
}) {
	const setConversationsById = useAppStore(
		(state) => state.setConversationsById,
	);
	const [selectedModel, setSelectedModel] = useState("");
	const [selectedThreadId, setSelectedThreadId] = useState("");
	const [thread, setThread] = useState(null);
	const [draft, setDraft] = useState("");
	const [error, setError] = useState(null);
	const [isOpening, setIsOpening] = useState(false);
	const [isSending, setIsSending] = useState(false);
	const [pendingMessage, setPendingMessage] = useState("");
	const [statusText, setStatusText] = useState("");
	const [showContext, setShowContext] = useState(false);
	const allThreads = Array.isArray(assistantMessage?.side_chats)
		? assistantMessage.side_chats
		: [];
	const matchingThreads = source
		? allThreads
				.filter((item) => threadMatchesSource(item, source, selectedModel))
				.sort((left, right) => {
					const rightKey = right.updated_at || right.created_at || "";
					const leftKey = left.updated_at || left.created_at || "";
					return rightKey.localeCompare(leftKey);
				})
		: [];
	const latestThreadId = matchingThreads[0]?.id || "";

	const persistThread = useCallback(
		(nextThread) => {
			if (!conversationId || assistantAbsoluteIndex == null || !nextThread) {
				return;
			}

			setConversationsById((previous) =>
				produce(previous, (draftState) => {
					const conversation = draftState[conversationId];
					const message = conversation?.messages?.[assistantAbsoluteIndex];
					if (!message) return;

					if (!Array.isArray(message.side_chats)) {
						message.side_chats = [];
					}

					const existingIndex = message.side_chats.findIndex(
						(item) => item.id === nextThread.id,
					);
					if (existingIndex >= 0) {
						message.side_chats[existingIndex] = nextThread;
						return;
					}

					message.side_chats.push(nextThread);
				}),
			);
		},
		[assistantAbsoluteIndex, conversationId, setConversationsById],
	);

	useEffect(() => {
		if (!isOpen || !source) return;
		const defaultModel = availableModels.includes(source.sourceModel)
			? source.sourceModel
			: availableModels[0] || "";
		setSelectedModel(defaultModel);
		setSelectedThreadId("");
		setThread(null);
		setDraft("");
		setError(null);
		setPendingMessage("");
		setStatusText("");
		setShowContext(false);
	}, [availableModels, isOpen, source]);

	useEffect(() => {
		if (!isOpen || !conversationId || !source || !selectedModel) return;
		if (!latestThreadId) {
			setIsOpening(false);
			setThread(null);
			setSelectedThreadId("");
			setError(null);
			setStatusText(
				"No saved thread yet. Start new or send a message to begin.",
			);
			return;
		}

		let cancelled = false;

		async function openLatestThread() {
			setIsOpening(true);
			setError(null);
			setStatusText("");

			try {
				const result = await api.openSideChat(conversationId, {
					message_index: assistantMessageIndex,
					source_type: source.sourceType,
					source_model: source.sourceModel || undefined,
					version_index: source.versionIndex ?? undefined,
					target_model: selectedModel,
					thread_id: latestThreadId,
				});
				if (cancelled) return;

				persistThread(result.thread);
				setThread(result.thread);
				setSelectedThreadId(result.thread.id);
				setStatusText(
					result.created
						? "Started a new saved thread."
						: "Resumed the latest saved thread.",
				);
			} catch (err) {
				if (cancelled) return;
				setError(err.message);
			} finally {
				if (!cancelled) {
					setIsOpening(false);
				}
			}
		}

		openLatestThread();
		return () => {
			cancelled = true;
		};
	}, [
		assistantMessageIndex,
		conversationId,
		isOpen,
		latestThreadId,
		persistThread,
		selectedModel,
		source,
	]);

	async function openSpecificThread(options = {}) {
		if (!conversationId || !source || !selectedModel) return;

		setIsOpening(true);
		setError(null);
		setStatusText("");

		try {
			const result = await api.openSideChat(conversationId, {
				message_index: assistantMessageIndex,
				source_type: source.sourceType,
				source_model: source.sourceModel || undefined,
				version_index: source.versionIndex ?? undefined,
				target_model: selectedModel,
				thread_id: options.threadId || undefined,
				start_new: Boolean(options.startNew),
			});

			persistThread(result.thread);
			setThread(result.thread);
			setSelectedThreadId(result.thread.id);
			setDraft("");
			setPendingMessage("");
			setStatusText(
				result.created
					? "Started a new saved thread."
					: "Opened a saved thread.",
			);
		} catch (err) {
			setError(err.message);
		} finally {
			setIsOpening(false);
		}
	}

	async function handleSend() {
		const content = draft.trim();
		if (!content || !selectedModel || isSending) return;

		setIsSending(true);
		setError(null);
		setPendingMessage(content);

		try {
			let activeThread = thread;
			if (!activeThread) {
				const opened = await api.openSideChat(conversationId, {
					message_index: assistantMessageIndex,
					source_type: source.sourceType,
					source_model: source.sourceModel || undefined,
					version_index: source.versionIndex ?? undefined,
					target_model: selectedModel,
					start_new: true,
				});
				persistThread(opened.thread);
				activeThread = opened.thread;
				setThread(opened.thread);
				setSelectedThreadId(opened.thread.id);
			}

			const result = await api.sendSideChatMessage(
				conversationId,
				activeThread.id,
				assistantMessageIndex,
				content,
			);
			persistThread(result.thread);
			setThread(result.thread);
			setSelectedThreadId(result.thread.id);
			setDraft("");
			setPendingMessage("");
			setStatusText("Saved to this thread.");
		} catch (err) {
			setError(err.message);
			setPendingMessage("");
		} finally {
			setIsSending(false);
		}
	}

	function handleComposerKeyDown(event) {
		if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
			event.preventDefault();
			handleSend();
		}
	}

	const effectivePrompt =
		thread?.context?.effective_prompt || source?.effectivePrompt || "";
	const sourceContent =
		thread?.context?.source_content || source?.sourcePreview || "";
	const sourceNote =
		thread?.source_note || getFallbackSourceNote(source?.sourceType);
	const visibleMessages = Array.isArray(thread?.messages)
		? thread.messages
		: [];

	return (
		<Dialog open={isOpen} onOpenChange={onOpenChange}>
			<DialogContent className="flex h-[min(94vh,64rem)] max-w-6xl flex-col overflow-hidden p-0">
				<DialogHeader className="border-b border-border/60 px-6 py-4">
					<div className="flex flex-wrap items-start justify-between gap-3">
						<div className="space-y-2">
							<DialogTitle>Side Chat with a Council Model</DialogTitle>
							<DialogDescription>
								Continue a focused one-model follow-up without re-running the
								full council.
							</DialogDescription>
						</div>
						<div className="flex flex-wrap items-center gap-2">
							<Badge variant="outline">
								{source?.sourceTitle || thread?.source_label || "Saved source"}
							</Badge>
							{selectedModel && (
								<Badge variant="outline">
									Target: {formatModelName(selectedModel)}
								</Badge>
							)}
						</div>
					</div>
				</DialogHeader>

				<div className="border-b border-border/60 px-6 py-4">
					<div className="flex flex-wrap items-end gap-4">
						<div className="min-w-[14rem] flex-1 space-y-2">
							<Label htmlFor="side-chat-model">Model</Label>
							<Select
								value={selectedModel}
								onValueChange={(value) => {
									setSelectedModel(value);
									setSelectedThreadId("");
									setThread(null);
									setDraft("");
									setPendingMessage("");
								}}
								disabled={availableModels.length === 0 || isSending}
							>
								<SelectTrigger id="side-chat-model">
									<SelectValue placeholder="Select a model" />
								</SelectTrigger>
								<SelectContent>
									{availableModels.map((model) => (
										<SelectItem key={model} value={model}>
											{formatModelName(model)}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</div>

						<div className="min-w-[16rem] flex-[1.4] space-y-2">
							<div className="flex items-center justify-between gap-2">
								<Label htmlFor="side-chat-thread">Saved Threads</Label>
								<span className="text-xs text-muted-foreground">
									{matchingThreads.length > 0
										? `${matchingThreads.length} saved`
										: "No saved threads yet"}
								</span>
							</div>
							{matchingThreads.length > 0 ? (
								<Select
									value={selectedThreadId || matchingThreads[0].id}
									onValueChange={(value) =>
										openSpecificThread({ threadId: value })
									}
									disabled={!selectedModel || isOpening || isSending}
								>
									<SelectTrigger id="side-chat-thread">
										<SelectValue placeholder="Select a saved thread" />
									</SelectTrigger>
									<SelectContent>
										{matchingThreads.map((item, index) => (
											<SelectItem key={item.id} value={item.id}>
												{buildThreadLabel(item, index)}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							) : (
								<div className="rounded-md border border-dashed border-border/60 px-3 py-2 text-sm text-muted-foreground">
									Threads are created when you start a new one or send the first
									follow-up.
								</div>
							)}
						</div>

						<div className="flex items-center gap-2 sm:ml-auto">
							<Button
								type="button"
								variant="outline"
								size="sm"
								onClick={() => setShowContext((current) => !current)}
							>
								{showContext ? "Hide Context" : "Show Context"}
							</Button>
							<Button
								type="button"
								variant="outline"
								size="sm"
								onClick={() => openSpecificThread({ startNew: true })}
								disabled={!selectedModel || isOpening || isSending}
							>
								Start New
							</Button>
						</div>
					</div>
				</div>

				{showContext && (
					<div className="border-b border-border/60 bg-muted/10 px-6 py-4">
						<div className="grid gap-3 lg:grid-cols-2">
							<div className="rounded-lg border border-border/60 bg-muted/40 p-3">
								<p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
									Effective Prompt
								</p>
								<div className="mt-2 max-h-32 overflow-y-auto text-sm text-foreground">
									<Markdown>
										{effectivePrompt || "Opening thread context…"}
									</Markdown>
								</div>
							</div>
							<div className="rounded-lg border border-border/60 bg-muted/40 p-3">
								<div className="flex items-center justify-between gap-2">
									<p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
										Source Snapshot
									</p>
									{sourceNote && (
										<span className="text-xs text-muted-foreground">
											{sourceNote}
										</span>
									)}
								</div>
								<div className="mt-2 max-h-32 overflow-y-auto text-sm text-foreground">
									<Markdown>
										{sourceContent || "Opening thread context…"}
									</Markdown>
								</div>
							</div>
						</div>
					</div>
				)}

				<div className="min-h-0 flex-1 overflow-y-auto bg-muted/10 px-6 py-5">
					{error && (
						<div className="mb-4 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
							{error}
						</div>
					)}

					{statusText && !error && (
						<div className="mb-4 rounded-lg border border-border/60 bg-background px-3 py-2 text-sm text-muted-foreground">
							{statusText}
						</div>
					)}

					{isOpening && !thread ? (
						<div className="rounded-lg border border-dashed border-border/60 bg-background px-4 py-6 text-sm text-muted-foreground">
							Opening saved side-chat context…
						</div>
					) : (
						<div className="space-y-4">
							{visibleMessages.length === 0 && !pendingMessage && (
								<div className="rounded-lg border border-dashed border-border/60 bg-background px-4 py-6 text-sm text-muted-foreground">
									This thread is ready. Ask a follow-up about the selected
									artifact.
								</div>
							)}

							{visibleMessages.map((message) => (
								<div
									key={message.id || `${message.role}-${message.created_at}`}
									className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
								>
									<div
										className={`max-w-[85%] rounded-2xl border px-4 py-3 shadow-sm ${
											message.role === "user"
												? "border-emerald-500/20 bg-emerald-500/10"
												: "border-border/60 bg-background"
										}`}
									>
										<div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
											<span className="font-semibold uppercase tracking-[0.16em]">
												{message.role === "user"
													? "You"
													: formatModelName(thread?.target_model)}
											</span>
											{message.created_at && (
												<span>{formatTimestamp(message.created_at)}</span>
											)}
										</div>
										<Markdown>{message.content}</Markdown>
									</div>
								</div>
							))}

							{pendingMessage && (
								<>
									<div className="flex justify-end">
										<div className="max-w-[85%] rounded-2xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 shadow-sm">
											<div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
												<span className="font-semibold uppercase tracking-[0.16em]">
													You
												</span>
											</div>
											<Markdown>{pendingMessage}</Markdown>
										</div>
									</div>
									<div className="flex justify-start">
										<div className="max-w-[85%] rounded-2xl border border-border/60 bg-background px-4 py-3 text-sm text-muted-foreground shadow-sm">
											{formatModelName(thread?.target_model || selectedModel)}{" "}
											is thinking…
										</div>
									</div>
								</>
							)}
						</div>
					)}
				</div>

				<div className="border-t border-border/60 px-6 py-4">
					<div className="space-y-3">
						<Textarea
							value={draft}
							onChange={(event) => setDraft(event.target.value)}
							onKeyDown={handleComposerKeyDown}
							placeholder="Ask a focused follow-up about this result…"
							disabled={!selectedModel || isOpening || isSending}
							className="min-h-[120px] resize-y"
						/>
						<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
							<p className="text-xs text-muted-foreground">
								Saved inside this consultation. Press Ctrl+Enter or Cmd+Enter to
								send.
							</p>
							<Button
								type="button"
								onClick={handleSend}
								disabled={
									!draft.trim() || !selectedModel || isOpening || isSending
								}
							>
								{isSending ? "Sending…" : "Send Follow-up"}
							</Button>
						</div>
					</div>
				</div>
			</DialogContent>
		</Dialog>
	);
}
