import { useCallback, useState, useEffect, useRef } from "react";
import { useShallow } from "zustand/react/shallow";
import Markdown from "./Markdown";
import Stage1 from "./Stage1";
import NegativeStage from "./NegativeStage";
import Stage2 from "./Stage2";
import DeliberationStage from "./DeliberationStage";
import Stage3 from "./Stage3";
import DebugTrace from "./DebugTrace";
import InteractivePrompt from "./InteractivePrompt";
import ModelSideChatModal from "./ModelSideChatModal";
import PromptRefinementSummary from "./PromptRefinementSummary";
import StageProgress from "./StageProgress";
import StatusModal from "./StatusModal";
import ApiKeyStatus from "./ApiKeyStatus";
import ThemeToggle from "./ThemeToggle";
import { Button } from "./ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "./ui/card";
import { Textarea } from "./ui/textarea";
import { Label } from "./ui/label";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { ScrollArea } from "./ui/scroll-area";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "./ui/alert-dialog";
import { cn } from "../lib/utils";
import { useAppStore } from "../store/useAppStore";

const DEFAULT_PROMPT_MAX_CHARS = 15000;
const DEFAULT_NEGATIVE_PROMPT_MAX_CHARS = 15000;
const DEFAULT_VISIBLE_MESSAGES = 50;
const NEGATIVE_PROMPT_EXAMPLES = [
	{
		label: "Risk review",
		prompt:
			"Find hidden assumptions, failure modes, missing caveats, and safety or compliance risks.",
	},
	{
		label: "Implementation review",
		prompt:
			"Look for unclear sequencing, missing dependencies, weak test coverage, and rollback gaps.",
	},
	{
		label: "Decision review",
		prompt:
			"Challenge the recommendation by naming tradeoffs, alternatives, weak evidence, and reversal conditions.",
	},
];

export default function ChatInterface({
	onSendMessage,
	onInteractiveSubmit,
	onInteractiveSkip,
	onApiKeyClear,
	onOpenSidebar,
	onCancelConsultation,
	onVersionAdded,
	onCurrentChanged,
}) {
	const {
		conversationsById,
		currentConversationId,
		isLoading,
		error,
		config,
		currentInteractivePrompt,
		consultationStatus,
		messageQueue,
		streamingConsultations,
		isCancelling,
		setError,
	} = useAppStore(
		useShallow((state) => ({
			conversationsById: state.conversationsById,
			currentConversationId: state.currentConversationId,
			isLoading: state.isLoading,
			error: state.error,
			config: state.config,
			currentInteractivePrompt: state.currentInteractivePrompt,
			consultationStatus: state.consultationStatus,
			messageQueue: state.messageQueue,
			streamingConsultations: state.streamingConsultations,
			isCancelling: state.isCancelling,
			setError: state.setError,
		})),
	);
	const conversation = currentConversationId
		? conversationsById[currentConversationId]
		: null;
	const canCancel = Boolean(
		currentConversationId &&
			(streamingConsultations[currentConversationId] ||
				(consultationStatus &&
					!["complete", "failed", "none"].includes(
						consultationStatus.status,
					)) ||
				messageQueue.length > 0),
	);
	const [input, setInput] = useState("");
	const [negativePrompt, setNegativePrompt] = useState("");
	const [inputError, setInputError] = useState(null);
	const [isStatusModalOpen, setIsStatusModalOpen] = useState(false);
	const [isStopConfirmOpen, setIsStopConfirmOpen] = useState(false);
	const [sideChatSource, setSideChatSource] = useState(null);
	const [statusAnnouncement, setStatusAnnouncement] = useState("");
	const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);
	const [visibleMessageCount, setVisibleMessageCount] = useState(
		DEFAULT_VISIBLE_MESSAGES,
	);
	const messagesEndRef = useRef(null);
	const promptMaxChars =
		Number.isFinite(config?.prompt_max_chars) && config.prompt_max_chars > 0
			? config.prompt_max_chars
			: DEFAULT_PROMPT_MAX_CHARS;
	const negativePromptMaxChars =
		Number.isFinite(config?.negative_prompt_max_chars) &&
		config.negative_prompt_max_chars > 0
			? config.negative_prompt_max_chars
			: DEFAULT_NEGATIVE_PROMPT_MAX_CHARS;
	const isNegativeModeDisabled = config?.negative_mode_enabled === false;
	const interactivePromptKey = currentInteractivePrompt
		? [
				currentInteractivePrompt.session_id ?? "no-session",
				currentInteractivePrompt.prompt_id ?? "no-prompt",
				currentInteractivePrompt.title ?? "",
			].join(":")
		: null;
	const conversationMessageCount = conversation?.messages?.length ?? 0;

	// Compute current loading stage for announcements
	const lastMessage =
		conversation?.messages?.[conversation.messages.length - 1];
	const hasFinalStage3 = Boolean(
		lastMessage?.role === "assistant" && lastMessage?.stage3,
	);
	const currentLoadingStage =
		lastMessage?.role === "assistant" && lastMessage?.loading
			? Object.entries(lastMessage.loading).find(([, v]) => v)?.[0]
			: null;

	// Announce loading state changes to screen readers
	useEffect(() => {
		const announcements = {
			stage1: "Stage 1: Collecting individual responses",
			negative: "Negative Mode: Scoring gaps and risks",
			stage2: "Stage 2: Peer rankings in progress",
			deliberation: "Deliberation: Models revising based on feedback",
			stage3: "Stage 3: Final synthesis in progress",
		};

		if (currentLoadingStage && announcements[currentLoadingStage]) {
			setStatusAnnouncement(announcements[currentLoadingStage]);
		} else if (!isLoading && statusAnnouncement) {
			setStatusAnnouncement("Consultation complete");
			// Clear after announcement
			const timer = setTimeout(() => setStatusAnnouncement(""), 1000);
			return () => clearTimeout(timer);
		}
	}, [currentLoadingStage, isLoading, statusAnnouncement]);

	useEffect(() => {
		if (typeof window === "undefined" || !window.matchMedia) return undefined;
		const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
		const handleChange = () => setPrefersReducedMotion(mediaQuery.matches);
		handleChange();
		if (mediaQuery.addEventListener) {
			mediaQuery.addEventListener("change", handleChange);
			return () => mediaQuery.removeEventListener("change", handleChange);
		}
		mediaQuery.addListener(handleChange);
		return () => mediaQuery.removeListener(handleChange);
	}, []);

	const scrollToBottom = useCallback(() => {
		messagesEndRef.current?.scrollIntoView({
			behavior: prefersReducedMotion ? "auto" : "smooth",
		});
	}, [prefersReducedMotion]);

	useEffect(() => {
		const timer = setTimeout(scrollToBottom, 100);
		return () => clearTimeout(timer);
	}, [conversationMessageCount, interactivePromptKey, scrollToBottom]);

	useEffect(() => {
		if (conversation?.id && conversationMessageCount === 0) {
			setInput("");
			setNegativePrompt("");
		}
	}, [conversation?.id, conversationMessageCount]);

	useEffect(() => {
		if (!conversation?.id) return;
		setVisibleMessageCount(DEFAULT_VISIBLE_MESSAGES);
		setSideChatSource(null);
	}, [conversation?.id]);

	const getEffectivePromptForAssistantMessage = (
		assistantMessage,
		fallbackUserPrompt,
	) => {
		const refinement = assistantMessage?.metadata?.prompt_refinement;
		if (!refinement) {
			return fallbackUserPrompt || "";
		}

		if (refinement.decision === "use_refined" && refinement.refined_prompt) {
			return refinement.refined_prompt;
		}

		return (
			refinement.original_prompt ||
			fallbackUserPrompt ||
			refinement.refined_prompt ||
			""
		);
	};

	const handleSubmit = (e) => {
		e.preventDefault();
		setInputError(null);

		const trimmedInput = input.trim();
		if (!trimmedInput) {
			setInputError("Please enter a prompt");
			return;
		}
		if (trimmedInput.length > promptMaxChars) {
			setInputError(`Prompt too long (max ${promptMaxChars} characters)`);
			return;
		}
		if (isCancelling) return;

		onSendMessage(input, negativePrompt);
		setInput("");
		setNegativePrompt("");
	};

	const handleKeyDown = (e) => {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			handleSubmit(e);
		}
	};

	const StatusBar = () => (
		<div className="chat-status-bar flex items-center justify-between gap-3 border-b border-border/60 bg-background/80 px-4 py-3 backdrop-blur-sm">
			<div className="flex items-center gap-3">
				{onOpenSidebar && (
					<Button
						variant="ghost"
						size="icon"
						className="console-sidebar-toggle lg:hidden"
						onClick={onOpenSidebar}
						aria-label="Open consultations"
					>
						<svg
							aria-hidden="true"
							viewBox="0 0 24 24"
							className="h-5 w-5"
							fill="none"
							stroke="currentColor"
							strokeWidth="2"
							strokeLinecap="round"
							strokeLinejoin="round"
						>
							<line x1="3" y1="12" x2="21" y2="12" />
							<line x1="3" y1="6" x2="21" y2="6" />
							<line x1="3" y1="18" x2="21" y2="18" />
						</svg>
					</Button>
				)}
				<div className="console-header-info">
					{conversation ? (
						<>
							<p className="console-label text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
								Council Console
							</p>
							<p
								className="console-title max-w-[240px] truncate text-base font-semibold leading-tight sm:max-w-[360px] lg:max-w-[520px]"
								title={conversation.title || "New Consultation"}
							>
								{conversation.title || "New Consultation"}
							</p>
						</>
					) : (
						<>
							<p className="console-label text-sm font-semibold">
								Council Console
							</p>
							<p className="console-tagline hidden text-xs text-muted-foreground sm:block">
								Streamed deliberation and synthesis
							</p>
						</>
					)}
				</div>
			</div>
			<div className="console-controls flex items-center gap-2">
				<ThemeToggle />
				<ApiKeyStatus onClear={onApiKeyClear} />
				{canCancel && (
					<Button
						onClick={() => setIsStopConfirmOpen(true)}
						variant="destructive"
						size="sm"
						disabled={isCancelling}
						title="Stop the current consultation"
						className="console-stop-btn"
					>
						{isCancelling ? "Stopping…" : "Stop"}
					</Button>
				)}
				<Button
					onClick={() => setIsStatusModalOpen(true)}
					variant="ghost"
					size="sm"
					aria-label="View system status"
					className="console-status-btn"
				>
					<svg
						aria-hidden="true"
						className="h-4 w-4"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path
							strokeLinecap="round"
							strokeLinejoin="round"
							strokeWidth={2}
							d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
						/>
					</svg>
					<span>Status</span>
				</Button>
			</div>
		</div>
	);

	const errorBanner = error && (
		<div className="px-4 pt-4">
			<Alert variant="destructive" className="flex items-start gap-3">
				<svg
					aria-hidden="true"
					className="mt-0.5 h-5 w-5 shrink-0"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					strokeWidth="2"
				>
					<circle cx="12" cy="12" r="10" />
					<line x1="12" y1="8" x2="12" y2="12" />
					<line x1="12" y1="16" x2="12.01" y2="16" />
				</svg>
				<div className="flex-1">
					<AlertTitle>{error.message}</AlertTitle>
					{error.details && (
						<AlertDescription className="text-destructive/80">
							{error.details}
						</AlertDescription>
					)}
				</div>
				<Button
					onClick={() => setError(null)}
					variant="ghost"
					size="icon"
					className="text-destructive"
					aria-label="Dismiss error"
				>
					<svg
						aria-hidden="true"
						viewBox="0 0 24 24"
						className="h-4 w-4"
						fill="none"
						stroke="currentColor"
						strokeWidth="2"
						strokeLinecap="round"
						strokeLinejoin="round"
					>
						<line x1="18" y1="6" x2="6" y2="18" />
						<line x1="6" y1="6" x2="18" y2="18" />
					</svg>
				</Button>
			</Alert>
		</div>
	);

	if (!conversation) {
		return (
			<div className="chat-interface flex min-h-[100dvh] flex-col lg:min-h-0">
				<StatusBar />
				<StatusModal
					config={config}
					isOpen={isStatusModalOpen}
					onClose={() => setIsStatusModalOpen(false)}
				/>
				{errorBanner}
				<div className="flex flex-1 items-center justify-center px-4 py-10">
					<Card className="chat-welcome-card w-full max-w-lg border-dashed bg-background/70">
						<CardHeader>
							<CardTitle className="welcome-title text-2xl">
								Welcome to LLM Council
							</CardTitle>
							<CardDescription className="welcome-description">
								Create a new consultation and let the council synthesize.
							</CardDescription>
						</CardHeader>
						<CardContent>
							<div className="welcome-instructions rounded-lg border border-border/60 bg-muted/60 p-4 text-sm text-muted-foreground">
								Start by opening the consultation list and choosing "New
								Consultation."
							</div>
						</CardContent>
					</Card>
				</div>
			</div>
		);
	}

	return (
		<div className="chat-interface flex min-h-[100dvh] flex-col lg:min-h-0">
			{/* Screen reader announcements for loading states */}
			<div
				role="status"
				aria-live="polite"
				aria-atomic="true"
				className="sr-announcement sr-only"
			>
				{statusAnnouncement}
			</div>
			<StatusBar />
			<AlertDialog open={isStopConfirmOpen} onOpenChange={setIsStopConfirmOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Stop this consultation?</AlertDialogTitle>
						<AlertDialogDescription>
							This will cancel the in-progress run and clear any queued messages
							for this consultation.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Keep running</AlertDialogCancel>
						<AlertDialogAction
							onClick={() => {
								setIsStopConfirmOpen(false);
								onCancelConsultation?.();
							}}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Stop now
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
			<StatusModal
				config={config}
				isOpen={isStatusModalOpen}
				onClose={() => setIsStatusModalOpen(false)}
			/>
			{errorBanner}

			<ScrollArea className="chat-scroll-area flex-1">
				<div className="chat-message-list flex flex-col gap-6 px-4 py-6 lg:px-6">
					{conversation.messages.length === 0 ? (
						<Card className="chat-empty-state border-dashed bg-background/70">
							<CardHeader>
								<CardTitle className="empty-state-title">
									Start a consultation
								</CardTitle>
								<CardDescription className="empty-state-description">
									Ask a question to consult the LLM Council.
								</CardDescription>
							</CardHeader>
						</Card>
					) : (
						(() => {
							const totalMessages = conversation.messages.length;
							const maxVisible = Math.min(visibleMessageCount, totalMessages);
							const startIndex = Math.max(0, totalMessages - maxVisible);
							const visibleMessages = conversation.messages.slice(startIndex);
							const hasHiddenMessages = startIndex > 0;

							// Pre-calculate assistant message indices
							const assistantIndices = new Map();
							let assistantCount = 0;
							conversation.messages.forEach((m, i) => {
								if (m.role === "assistant") {
									assistantIndices.set(i, assistantCount);
									assistantCount++;
								}
							});
							const requestNumbers = new Map();
							let requestCount = 0;
							conversation.messages.forEach((m, i) => {
								if (m.role === "user") {
									requestCount++;
								}
								requestNumbers.set(i, requestCount);
							});
							return (
								<>
									{hasHiddenMessages && (
										<div className="flex justify-center">
											<Button
												type="button"
												variant="outline"
												size="sm"
												onClick={() => {
													setVisibleMessageCount((prev) =>
														Math.min(
															prev + DEFAULT_VISIBLE_MESSAGES,
															totalMessages,
														),
													);
												}}
											>
												Load earlier messages
											</Button>
										</div>
									)}
									{visibleMessages.map((msg, index) => {
										const absoluteIndex = index + startIndex;
										const hasDeliberationResults =
											msg.metadata?.deliberation?.results != null;
										const assistantMessageIndex =
											assistantIndices.get(absoluteIndex);
										const previousUserMessage =
											conversation.messages[absoluteIndex - 1];
										const effectivePrompt =
											msg.role === "assistant"
												? getEffectivePromptForAssistantMessage(
														msg,
														previousUserMessage?.role === "user"
															? previousUserMessage.content
															: "",
													)
												: "";
										const requestNumber = requestNumbers.get(absoluteIndex);
										const requestLabel = requestNumber
											? `Request ${requestNumber}`
											: "Request";
										const responseLabel = requestNumber
											? `Synthesis for Request ${requestNumber}`
											: "Synthesis";
										return (
											<div
												key={absoluteIndex}
												className="animate-fade-up motion-reduce:animate-none"
											>
												{msg.role === "user" ? (
													(() => {
														// Check if the next message (assistant) has prompt refinement data
														const nextMsg =
															conversation.messages[absoluteIndex + 1];
														const refinementData =
															nextMsg?.metadata?.prompt_refinement;

														return (
															<section className="chat-message-user rounded-xl border border-border bg-card p-4 shadow-sm">
																<div className="message-header flex flex-wrap items-baseline justify-between gap-2">
																	<div>
																		<p className="message-type-label text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
																			Prompt
																		</p>
																		<p className="message-request-label text-sm font-semibold text-foreground">
																			{requestLabel}
																		</p>
																	</div>
																</div>
																<div className="message-content mt-3">
																	<Markdown>{msg.content}</Markdown>
																</div>
																<PromptRefinementSummary
																	refinementData={refinementData}
																/>
															</section>
														);
													})()
												) : (
													<section className="chat-message-assistant rounded-xl border border-border bg-muted/30 p-4 shadow-sm">
														<div className="message-header flex flex-wrap items-baseline justify-between gap-2">
															<div>
																<p className="message-type-label text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
																	Council Output
																</p>
																<p className="message-response-label text-sm font-semibold text-foreground">
																	{responseLabel}
																</p>
															</div>
														</div>
														<div className="stages-container mt-4 flex w-full flex-col gap-4">
															{(msg.loading?.stage1 || msg.stage1) && (
																<div className="stage-block stage-block-stage1">
																	{msg.loading?.stage1 && (
																		<Card className="stage-loading stage-loading-stage1 border-dashed bg-muted/50">
																			<CardContent className="stage-loading-content flex flex-col gap-2 p-4 text-sm text-muted-foreground">
																				<div className="flex items-center gap-3">
																					<span
																						className="spinner"
																						aria-hidden="true"
																					/>
																					<span className="stage-loading-text">
																						Running Stage 1: Collecting
																						individual responses…
																					</span>
																				</div>
																				<StageProgress
																					progress={
																						msg.metadata?.stage_progress?.stage1
																					}
																				/>
																			</CardContent>
																		</Card>
																	)}
																	{msg.stage1 && (
																		<Stage1
																			responses={msg.stage1}
																			onStartSideChat={(payload) =>
																				setSideChatSource({
																					...payload,
																					effectivePrompt,
																					assistantAbsoluteIndex: absoluteIndex,
																					assistantMessageIndex,
																				})
																			}
																		/>
																	)}
																</div>
															)}

															{(msg.loading?.negative || msg.negative) && (
																<div className="stage-block stage-block-negative">
																	{msg.loading?.negative && (
																		<Card className="stage-loading stage-loading-negative border-dashed bg-muted/50">
																			<CardContent className="stage-loading-content flex flex-col gap-2 p-4 text-sm text-muted-foreground">
																				<div className="flex items-center gap-3">
																					<span
																						className="spinner"
																						aria-hidden="true"
																					/>
																					<span className="stage-loading-text">
																						Running Negative Mode: scoring gaps
																						and risks…
																					</span>
																				</div>
																				<StageProgress
																					progress={
																						msg.metadata?.stage_progress
																							?.negative
																					}
																				/>
																			</CardContent>
																		</Card>
																	)}
																	{msg.negative && (
																		<NegativeStage
																			negativeContext={msg.negative}
																			labelToModel={
																				msg.metadata?.label_to_model
																			}
																			aggregateGapScores={
																				msg.metadata?.aggregate_gap_scores
																			}
																		/>
																	)}
																</div>
															)}

															{(msg.loading?.stage2 || msg.stage2) && (
																<div className="stage-block stage-block-stage2">
																	{msg.loading?.stage2 && (
																		<Card className="stage-loading stage-loading-stage2 border-dashed bg-muted/50">
																			<CardContent className="stage-loading-content flex flex-col gap-2 p-4 text-sm text-muted-foreground">
																				<div className="flex items-center gap-3">
																					<span
																						className="spinner"
																						aria-hidden="true"
																					/>
																					<span className="stage-loading-text">
																						Running Stage 2: Peer rankings…
																					</span>
																				</div>
																				<StageProgress
																					progress={
																						msg.metadata?.stage_progress?.stage2
																					}
																				/>
																			</CardContent>
																		</Card>
																	)}
																	{msg.stage2 && (
																		<Stage2
																			rankings={msg.stage2}
																			labelToModel={
																				msg.metadata?.label_to_model
																			}
																			aggregateRankings={
																				msg.metadata?.aggregate_rankings
																			}
																			conversationId={conversation.id}
																			messageIndex={assistantMessageIndex}
																		/>
																	)}
																</div>
															)}

															{((msg.loading?.deliberation &&
																!hasDeliberationResults) ||
																(msg.metadata?.deliberation?.enabled &&
																	(msg.metadata?.deliberation?.results
																		?.length ?? 0) > 0)) && (
																<div className="stage-block stage-block-deliberation">
																	{msg.loading?.deliberation &&
																		!hasDeliberationResults && (
																			<Card className="stage-loading stage-loading-deliberation border-dashed bg-muted/50">
																				<CardContent className="stage-loading-content flex flex-col gap-2 p-4 text-sm text-muted-foreground">
																					<div className="flex items-center gap-3">
																						<span
																							className="spinner"
																							aria-hidden="true"
																						/>
																						<span className="stage-loading-text">
																							Running Deliberation: Models
																							revising based on feedback…
																						</span>
																					</div>
																					<StageProgress
																						progress={
																							msg.metadata?.stage_progress
																								?.deliberation
																						}
																					/>
																				</CardContent>
																			</Card>
																		)}
																	{msg.metadata?.deliberation && (
																		<DeliberationStage
																			deliberation={msg.metadata.deliberation}
																			onStartSideChat={(payload) =>
																				setSideChatSource({
																					...payload,
																					effectivePrompt,
																					assistantAbsoluteIndex: absoluteIndex,
																					assistantMessageIndex,
																				})
																			}
																		/>
																	)}
																</div>
															)}

															{(msg.loading?.stage3 || msg.stage3) && (
																<div className="stage-block stage-block-stage3">
																	{msg.loading?.stage3 && !msg.stage3 && (
																		<Card className="stage-loading stage-loading-stage3 border-dashed bg-muted/50">
																			<CardContent className="stage-loading-content flex flex-col gap-2 p-4 text-sm text-muted-foreground">
																				<div className="flex items-center gap-3">
																					<span
																						className="spinner"
																						aria-hidden="true"
																					/>
																					<span className="stage-loading-text">
																						Running Stage 3: Final synthesis…
																					</span>
																				</div>
																				<StageProgress
																					progress={
																						msg.metadata?.stage_progress?.stage3
																					}
																				/>
																			</CardContent>
																		</Card>
																	)}
																	{msg.stage3 && (
																		<Stage3
																			finalResponse={msg.stage3}
																			conversationId={conversation.id}
																			messageIndex={assistantMessageIndex}
																			onStartSideChat={(payload) =>
																				setSideChatSource({
																					...payload,
																					effectivePrompt,
																					assistantAbsoluteIndex: absoluteIndex,
																					assistantMessageIndex,
																				})
																			}
																			onVersionAdded={(version, options) => {
																				if (onVersionAdded) {
																					onVersionAdded(
																						absoluteIndex,
																						version,
																						options,
																					);
																				}
																			}}
																			onCurrentChanged={(versionIndex) => {
																				if (onCurrentChanged) {
																					onCurrentChanged(
																						absoluteIndex,
																						versionIndex,
																					);
																				}
																			}}
																		/>
																	)}
																</div>
															)}

															{msg.trace && (
																<div className="stage-block stage-block-trace">
																	<DebugTrace trace={msg.trace} />
																</div>
															)}
														</div>
													</section>
												)}
											</div>
										);
									})}
								</>
							);
						})()
					)}

					{isLoading && !currentInteractivePrompt && !hasFinalStage3 && (
						<Card className="council-loading border-dashed bg-muted/50">
							<CardContent className="council-loading-content flex items-center gap-3 p-4 text-sm text-muted-foreground">
								<span className="spinner" aria-hidden="true" />
								<span className="council-loading-text">
									Consulting the council…
								</span>
							</CardContent>
						</Card>
					)}

					{currentInteractivePrompt && (
						<InteractivePrompt
							key={interactivePromptKey}
							prompt={currentInteractivePrompt}
							onSubmit={onInteractiveSubmit}
							onSkip={onInteractiveSkip}
						/>
					)}

					<ModelSideChatModal
						isOpen={Boolean(sideChatSource)}
						onOpenChange={(open) => {
							if (!open) {
								setSideChatSource(null);
							}
						}}
						conversationId={conversation.id}
						assistantAbsoluteIndex={sideChatSource?.assistantAbsoluteIndex}
						assistantMessageIndex={sideChatSource?.assistantMessageIndex}
						assistantMessage={
							sideChatSource
								? conversation.messages[sideChatSource.assistantAbsoluteIndex]
								: null
						}
						source={sideChatSource}
						availableModels={config?.council_models || []}
					/>

					<div ref={messagesEndRef} />
				</div>
			</ScrollArea>

			<form
				className="chat-input-form border-t border-border/60 bg-background/80 px-4 py-5 backdrop-blur-sm"
				onSubmit={handleSubmit}
			>
				<div className="chat-input-fields grid gap-4 lg:grid-cols-2">
					<div className="prompt-input-group space-y-2">
						<div className="prompt-input-header flex items-center justify-between">
							<Label htmlFor="prompt-input" className="prompt-input-label">
								Prompt
							</Label>
							<span className="prompt-char-count text-xs text-muted-foreground">
								{input.length}/{promptMaxChars}
							</span>
						</div>
						<Textarea
							id="prompt-input"
							name="prompt"
							className={cn(
								"chat-prompt-input min-h-[120px] resize-y",
								inputError &&
									"border-destructive focus-visible:ring-destructive/40",
							)}
							placeholder={
								'Ask your question… e.g., "Summarize the risks of migrating X." (Shift+Enter for new line)'
							}
							value={input}
							onChange={(e) => {
								setInput(e.target.value);
								setInputError(null);
							}}
							onKeyDown={handleKeyDown}
							disabled={isCancelling}
							maxLength={promptMaxChars}
							autoComplete="off"
							aria-describedby="prompt-error"
						/>
						<p
							id="prompt-error"
							role="alert"
							aria-live="assertive"
							className={cn(
								"prompt-error-message text-xs text-destructive",
								!inputError && "sr-only",
							)}
						>
							{inputError || "\u00A0"}
						</p>
					</div>
					<div className="negative-prompt-input-group space-y-2">
						<div className="negative-prompt-input-header flex items-center justify-between">
							<Label
								htmlFor="negative-prompt-input"
								className="negative-prompt-input-label"
							>
								Negative Mode instructions (optional)
							</Label>
							<span className="negative-prompt-char-count text-xs text-muted-foreground">
								{negativePrompt.length}/{negativePromptMaxChars}
							</span>
						</div>
						<Textarea
							id="negative-prompt-input"
							name="negativePrompt"
							className="chat-negative-prompt-input min-h-[120px] resize-y"
							placeholder={
								"Optional: tell the critique stage what to stress-test."
							}
							value={negativePrompt}
							onChange={(e) => setNegativePrompt(e.target.value)}
							onKeyDown={handleKeyDown}
							disabled={isCancelling}
							maxLength={negativePromptMaxChars}
							autoComplete="off"
							aria-describedby="negative-prompt-helper negative-prompt-examples"
						/>
						<p
							id="negative-prompt-helper"
							className="negative-prompt-helper-text text-xs text-muted-foreground"
						>
							{isNegativeModeDisabled
								? "Negative Mode is disabled in backend config, so this field will be ignored until it is enabled."
								: "Controls the optional Negative Mode critique stage: after initial answers, reviewers use this instruction to find gaps before peer review and final synthesis. Leave blank to let the app generate a general gap-finding prompt."}
						</p>
						<div
							id="negative-prompt-examples"
							className="negative-prompt-examples space-y-2"
						>
							<p className="text-xs font-medium text-foreground">Examples</p>
							<div className="grid gap-2">
								{NEGATIVE_PROMPT_EXAMPLES.map((example) => (
									<button
										key={example.label}
										type="button"
										className="rounded-md border border-border/70 bg-muted/30 px-3 py-2 text-left text-xs transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
										onClick={() => setNegativePrompt(example.prompt)}
										disabled={isCancelling}
									>
										<span className="block font-medium text-foreground">
											{example.label}
										</span>
										<span className="block text-muted-foreground">
											{example.prompt}
										</span>
									</button>
								))}
							</div>
						</div>
					</div>
				</div>
				<div className="chat-input-footer mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
					<p className="chat-input-hint text-xs text-muted-foreground">
						Press Enter to send, Shift+Enter for a new line.
					</p>
					<Button
						type="submit"
						disabled={!input.trim() || isCancelling}
						className="chat-send-btn sm:min-w-[160px]"
					>
						<span className="chat-send-btn-text">
							{isLoading ? "Queue for Council" : "Send to Council"}
						</span>
					</Button>
				</div>
			</form>
		</div>
	);
}
