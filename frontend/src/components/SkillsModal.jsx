import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import Markdown from "./Markdown";
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
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import {
	Dialog,
	DialogClose,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "./ui/dialog";
import { Label } from "./ui/label";
import { cn } from "../lib/utils";

function formatChars(chars) {
	const value = Number(chars);
	if (!Number.isFinite(value) || value <= 0) return "0 chars";
	if (value < 1000) return `${value} chars`;
	return `${(value / 1000).toFixed(1)}k chars`;
}

function formatBytes(bytes) {
	const value = Number(bytes);
	if (!Number.isFinite(value) || value <= 0) return "0 B";
	if (value < 1024) return `${value} B`;
	if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
	return `${(value / (1024 * 1024)).toFixed(1)} MB`;
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

function referenceLabel(count) {
	return count === 1 ? "1 reference" : `${count} references`;
}

function countsOf(skill) {
	const counts = skill?.counts || {};
	return {
		references: Number(counts.references) || 0,
		scripts: Number(counts.scripts) || 0,
		assets: Number(counts.assets) || 0,
		evals: Number(counts.evals) || 0,
		other: Number(counts.other) || 0,
	};
}

function CloseIcon() {
	return (
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
	);
}

function TrashIcon() {
	return (
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
			<polyline points="3 6 5 6 21 6" />
			<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
			<path d="M10 11v6" />
			<path d="M14 11v6" />
			<path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
		</svg>
	);
}

function SkillRow({
	skill,
	isSelected,
	onSelect,
	onToggleEnabled,
	onRequestDelete,
	busy,
}) {
	const counts = countsOf(skill);
	const referenceCount = Number(skill.reference_count) || counts.references;
	const bundledUnused =
		Number(skill.bundled_unused_count) ||
		counts.scripts + counts.assets + counts.evals + counts.other;
	const warnings = Array.isArray(skill.warnings) ? skill.warnings : [];
	const displayName = skill.name || skill.id;
	const enabledId = `skill-enabled-${skill.id}`;

	return (
		<li
			className={cn(
				"skill-row flex items-start gap-3 rounded-lg border p-3 transition",
				isSelected
					? "skill-row--active border-primary/50 bg-primary/10"
					: "border-border/60 bg-card/50 hover:border-border hover:bg-muted/60",
			)}
			data-skill-id={skill.id}
			data-active={isSelected || undefined}
		>
			<button
				type="button"
				onClick={() => onSelect(skill.id)}
				className="skill-row-select min-w-0 flex-1 text-left"
				aria-pressed={isSelected}
				aria-label={`Show details for skill: ${displayName}`}
			>
				<span className="skill-row-head flex flex-wrap items-center gap-2">
					<span className="skill-row-name truncate text-sm font-semibold text-foreground">
						{displayName}
					</span>
					<Badge variant="outline" className="text-[10px]">
						{skill.source && skill.source !== "uploaded"
							? `${skill.source} · `
							: ""}
						v{skill.version ?? 1}
					</Badge>
					{skill.enabled === false && (
						<Badge variant="subtle" className="text-[10px]">
							Disabled
						</Badge>
					)}
					{warnings.length > 0 && (
						<Badge variant="warning" className="text-[10px]">
							{warnings.length === 1
								? "1 warning"
								: `${warnings.length} warnings`}
						</Badge>
					)}
				</span>
				{skill.description && (
					<span className="skill-row-description mt-1 line-clamp-2 block text-xs text-muted-foreground">
						{skill.description}
					</span>
				)}
				<span className="skill-row-meta mt-1 block text-[11px] text-muted-foreground">
					{referenceLabel(referenceCount)} · {formatChars(skill.skill_md_chars)}
					{bundledUnused > 0 ? ` · ${bundledUnused} bundled files unused` : ""}
				</span>
			</button>

			<div className="skill-row-actions flex shrink-0 items-center gap-2">
				<span className="flex items-center gap-1.5">
					<Checkbox
						id={enabledId}
						checked={skill.enabled !== false}
						disabled={busy}
						onCheckedChange={(checked) =>
							onToggleEnabled(skill, checked === true)
						}
					/>
					<Label
						htmlFor={enabledId}
						className="cursor-pointer text-xs text-muted-foreground"
					>
						Enabled
					</Label>
				</span>
				<Button
					type="button"
					variant="ghost"
					size="icon"
					className="skill-delete-btn h-9 w-9 shrink-0 text-destructive/70 hover:bg-destructive/10 hover:text-destructive"
					disabled={busy || (skill.source && skill.source !== "uploaded")}
					onClick={() => onRequestDelete(skill)}
					title="Delete skill"
					aria-label={`Delete skill: ${displayName}`}
				>
					<TrashIcon />
				</Button>
			</div>
		</li>
	);
}

function SkillDetail({ skill, markdown, isLoadingDetail }) {
	if (!skill) {
		return (
			<p className="skills-detail-empty rounded-lg border border-dashed border-border/60 bg-muted/20 px-4 py-6 text-sm text-muted-foreground">
				Select a skill to see its description, references, and bundled files.
			</p>
		);
	}

	const counts = countsOf(skill);
	const references = Array.isArray(skill.references) ? skill.references : null;
	const referenceCount = references
		? references.length
		: Number(skill.reference_count) || counts.references;
	const bundledUnused =
		Number(skill.bundled_unused_count) ||
		counts.scripts + counts.assets + counts.evals + counts.other;
	const warnings = Array.isArray(skill.warnings) ? skill.warnings : [];
	const updatedAt = formatTimestamp(skill.updated_at);

	return (
		<div className="skills-detail-body space-y-4">
			<div className="skills-detail-head space-y-1">
				<h3 className="text-sm font-semibold text-foreground">
					{skill.name || skill.id}
				</h3>
				<p className="font-mono text-xs text-muted-foreground">
					{skill.id} · v{skill.version ?? 1}
					{updatedAt ? ` · updated ${updatedAt}` : ""}
				</p>
			</div>

			{skill.description && (
				<p className="skills-detail-description text-sm text-foreground">
					{skill.description}
				</p>
			)}

			{warnings.length > 0 && (
				<ul className="skills-detail-warnings space-y-1">
					{warnings.map((warning, index) => (
						<li
							key={index}
							className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-900/90 dark:text-amber-100"
						>
							{warning}
						</li>
					))}
				</ul>
			)}

			<section className="skills-detail-references space-y-2">
				<h4 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
					References ({referenceCount})
				</h4>
				{referenceCount === 0 ? (
					<p className="text-xs text-muted-foreground">
						This skill has no{" "}
						<code className="rounded bg-muted px-1">references/</code> files —
						only SKILL.md is injected.
					</p>
				) : references ? (
					<ul className="space-y-2">
						{references.map((reference) => {
							const headings = Array.isArray(reference.headings)
								? reference.headings
								: [];
							return (
								<li
									key={reference.path}
									className="rounded-lg border border-border/60 bg-background/70 p-3"
								>
									<div className="flex flex-wrap items-baseline justify-between gap-2">
										<span className="font-mono text-xs text-foreground">
											{reference.path}
										</span>
										<span className="text-[11px] text-muted-foreground">
											{formatChars(reference.chars)}
										</span>
									</div>
									{reference.summary && (
										<p className="mt-1 text-xs text-muted-foreground">
											{reference.summary}
										</p>
									)}
									{headings.length > 0 && (
										<div className="mt-2 flex flex-wrap gap-1">
											{headings.map((heading) => (
												<Badge
													key={heading}
													variant="subtle"
													className="max-w-full truncate text-[10px] font-normal"
												>
													{heading}
												</Badge>
											))}
										</div>
									)}
								</li>
							);
						})}
					</ul>
				) : (
					<p className="text-xs text-muted-foreground">
						{referenceLabel(referenceCount)} available. A cheap router picks
						which ones a given question needs.
					</p>
				)}
			</section>

			<section className="skills-detail-markdown space-y-2">
				<h4 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
					SKILL.md
				</h4>
				{isLoadingDetail && !markdown ? (
					<p className="text-xs text-muted-foreground">Loading SKILL.md…</p>
				) : markdown ? (
					<div className="skills-detail-markdown-body max-h-72 overflow-y-auto rounded-lg border border-border/60 bg-background/70 p-3">
						<Markdown>{markdown}</Markdown>
					</div>
				) : (
					<p className="text-xs text-muted-foreground">
						SKILL.md preview unavailable.
					</p>
				)}
			</section>

			<section className="skills-detail-bundled space-y-2">
				<h4 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
					Bundled Files
				</h4>
				<p className="skills-detail-bundled-line text-sm text-foreground">
					{bundledUnused} bundled {bundledUnused === 1 ? "file" : "files"}{" "}
					unused ({counts.scripts} scripts / {counts.assets} assets /{" "}
					{counts.evals} evals)
				</p>
				<p className="text-xs text-muted-foreground">
					These files are stored for fidelity but never executed and never
					injected into a prompt.{" "}
					<code className="rounded bg-muted px-1">evals/</code> is an answer key
					and is never sent to any model.
				</p>
				{Array.isArray(skill.bundled_files) &&
					skill.bundled_files.length > 0 && (
						<ul className="space-y-1">
							{skill.bundled_files.map((file) => (
								<li
									key={file.path}
									className="flex flex-wrap items-baseline justify-between gap-2 rounded-md border border-border/60 bg-background/70 px-3 py-1.5"
								>
									<span className="font-mono text-[11px] text-muted-foreground">
										{file.path}
									</span>
									<span className="text-[11px] text-muted-foreground">
										{file.category} · {formatBytes(file.size_bytes)}
									</span>
								</li>
							))}
						</ul>
					)}
			</section>
		</div>
	);
}

/**
 * Agent Skills library manager: upload, enable/disable, inspect, and delete skills.
 * All async work is performed by the caller through the onUpload / onDelete /
 * onToggleEnabled callbacks.
 */
export default function SkillsModal({
	isOpen,
	onClose,
	skills,
	onUpload,
	sources = { codex: false, claude: false },
	onSourcesChange,
	onDelete,
	onToggleEnabled,
	focusSkillId,
	defaultSkillIds = [],
	onDefaultSkillIdsChange = () => {},
}) {
	const [selectedId, setSelectedId] = useState("");
	const [isUploading, setIsUploading] = useState(false);
	const [isUpdatingSources, setIsUpdatingSources] = useState(false);
	const [isDragging, setIsDragging] = useState(false);
	const [actionError, setActionError] = useState("");
	const [actionNotice, setActionNotice] = useState("");
	const [deleteTarget, setDeleteTarget] = useState(null);
	const [detailById, setDetailById] = useState({});
	const [markdownById, setMarkdownById] = useState({});
	const [isLoadingDetail, setIsLoadingDetail] = useState(false);
	const fileInputRef = useRef(null);

	useEffect(() => {
		if (!isOpen) return;
		setActionError("");
		setActionNotice("");
		setIsDragging(false);
	}, [isOpen]);

	useEffect(() => {
		if (!isOpen || !focusSkillId) return;
		setSelectedId(focusSkillId);
	}, [isOpen, focusSkillId]);

	const skillList = Array.isArray(skills) ? skills : [];
	const defaultIds = Array.isArray(defaultSkillIds) ? defaultSkillIds : [];
	const selectedSummary =
		skillList.find((skill) => skill.id === selectedId) || null;
	// listSkills returns to_summary() payloads, which carry no references or bundled
	// files. Fetch the detail (and SKILL.md) for the selected row and prefer it, falling
	// back to the summary while the request is in flight.
	const selectedSkill = selectedSummary
		? detailById[selectedId] || selectedSummary
		: null;
	const selectedMarkdown = markdownById[selectedId] || "";

	useEffect(() => {
		if (!isOpen || !selectedId) return;
		if (detailById[selectedId] && markdownById[selectedId]) return;

		let cancelled = false;
		setIsLoadingDetail(true);
		Promise.all([
			detailById[selectedId] ? Promise.resolve(null) : api.getSkill(selectedId),
			markdownById[selectedId]
				? Promise.resolve(null)
				: api.getSkillMarkdown(selectedId),
		])
			.then(([detail, body]) => {
				if (cancelled) return;
				if (detail)
					setDetailById((prev) => ({ ...prev, [selectedId]: detail }));
				if (body) {
					setMarkdownById((prev) => ({
						...prev,
						[selectedId]: body.markdown || "",
					}));
				}
			})
			.catch(() => {
				// A failed preview must never break the library UI; the summary still renders.
			})
			.finally(() => {
				if (!cancelled) setIsLoadingDetail(false);
			});

		return () => {
			cancelled = true;
		};
	}, [isOpen, selectedId, detailById, markdownById]);

	const uploadFile = async (file) => {
		if (!file) return;
		setActionError("");
		setActionNotice("");

		if (!/\.zip$/i.test(file.name)) {
			setActionError("Only .zip archives can be uploaded.");
			return;
		}

		setIsUploading(true);
		try {
			const result = await onUpload?.(file);
			const installed = result?.skill;
			if (result?.skills?.length > 1) {
				setDetailById({});
				setMarkdownById({});
				setSelectedId(installed?.id || "");
				setActionNotice(
					`Installed ${result.skills.length} skills${result.replaced ? " (including replacements)" : ""}.`,
				);
				return;
			}
			if (installed?.id) {
				// POST returns to_detail(), so seed the cache and invalidate the old preview.
				setDetailById((prev) => ({ ...prev, [installed.id]: installed }));
				setMarkdownById((prev) => {
					const next = { ...prev };
					delete next[installed.id];
					return next;
				});
				setSelectedId(installed.id);
				setActionNotice(
					result.replaced
						? `Replaced ${installed.id} (now v${installed.version ?? 1}).`
						: `Installed ${installed.id}.`,
				);
			}
		} catch (err) {
			setActionError(err?.message || "Failed to upload skill.");
		} finally {
			setIsUploading(false);
		}
	};

	const handleInputChange = async (event) => {
		const file = event.target.files?.[0];
		event.target.value = "";
		await uploadFile(file);
	};

	const handleDragOver = (event) => {
		event.preventDefault();
		if (isUploading) return;
		setIsDragging(true);
	};

	const handleDragLeave = (event) => {
		event.preventDefault();
		setIsDragging(false);
	};

	const handleDrop = async (event) => {
		event.preventDefault();
		setIsDragging(false);
		if (isUploading) return;
		await uploadFile(event.dataTransfer?.files?.[0]);
	};

	const handleToggleEnabled = async (skill, enabled) => {
		setActionError("");
		setActionNotice("");
		try {
			await onToggleEnabled?.(skill.id, enabled);
		} catch (err) {
			setActionError(err?.message || "Failed to update skill.");
		}
	};

	const handleConfirmDelete = async () => {
		const target = deleteTarget;
		setDeleteTarget(null);
		if (!target) return;
		setActionError("");
		setActionNotice("");
		try {
			await onDelete?.(target.id);
			setDetailById((prev) => {
				const next = { ...prev };
				delete next[target.id];
				return next;
			});
			setMarkdownById((prev) => {
				const next = { ...prev };
				delete next[target.id];
				return next;
			});
			if (selectedId === target.id) {
				setSelectedId("");
			}
			setActionNotice(`Deleted ${target.id}.`);
		} catch (err) {
			setActionError(err?.message || "Failed to delete skill.");
		}
	};

	return (
		<Dialog
			open={isOpen}
			onOpenChange={(open) => {
				if (!open) onClose();
			}}
		>
			<DialogContent className="skills-modal flex h-[min(92vh,60rem)] max-w-5xl flex-col overflow-hidden p-0 max-lg:overflow-y-auto">
				<DialogHeader className="skills-modal-header shrink-0 border-b border-border/60 px-6 py-4">
					<div className="flex items-start justify-between gap-3">
						<div className="min-w-0 flex-1 space-y-1">
							<DialogTitle>Agent Skills</DialogTitle>
							<DialogDescription>
								Upload individual skills or combined skill packs as .zip.
								Default skills apply to every chat; the composer can turn them
								off for a single chat.
							</DialogDescription>
						</div>
						<DialogClose asChild>
							<Button variant="ghost" size="icon" aria-label="Close">
								<CloseIcon />
							</Button>
						</DialogClose>
					</div>
				</DialogHeader>

				<div className="shrink-0 space-y-2 border-b border-border/60 px-6 py-3">
					<p className="text-sm font-medium">Local skill sources</p>
					<p className="text-xs text-muted-foreground">
						Read skills from the computer running the council. Original files
						stay unchanged. Applies to all council users.
					</p>
					<div className="flex flex-wrap gap-4">
						{["codex", "claude"].map((source) => (
							<label key={source} className="flex items-center gap-2 text-sm">
								<Checkbox
									checked={sources[source] === true}
									disabled={isUpdatingSources || isUploading}
									onCheckedChange={async (checked) => {
										setIsUpdatingSources(true);
										setActionError("");
										try {
											await onSourcesChange?.({
												...sources,
												[source]: checked === true,
											});
											setDetailById({});
											setMarkdownById({});
										} catch (error) {
											setActionError(
												error?.message || "Failed to update skill sources.",
											);
										} finally {
											setIsUpdatingSources(false);
										}
									}}
								/>
								{`~/.${source}/skills`}
							</label>
						))}
					</div>
				</div>
				<div className="skills-modal-upload shrink-0 border-b border-border/60 px-6 py-4">
					<div
						className={cn(
							"skills-dropzone rounded-lg border border-dashed px-4 py-5 text-center transition",
							isDragging
								? "border-primary/60 bg-primary/5"
								: "border-border/60 bg-muted/20",
						)}
						onDragOver={handleDragOver}
						onDragLeave={handleDragLeave}
						onDrop={handleDrop}
					>
						<p className="text-sm text-foreground">
							Drop a skill <code className="rounded bg-muted px-1">.zip</code>{" "}
							here
						</p>
						<p className="mx-auto mt-1 max-w-xl text-xs text-muted-foreground">
							Accepts individual skills and combined thinking-skills or
							business-writing-skills packs. Each skill keeps its references.
							Scripts, assets, and evals are never sent to a model.
						</p>
						<div className="mt-3 flex items-center justify-center">
							<Button
								type="button"
								variant="outline"
								size="sm"
								className="skills-upload-btn"
								onClick={() => fileInputRef.current?.click()}
								disabled={isUploading}
							>
								{isUploading ? "Uploading…" : "Choose .zip file"}
							</Button>
						</div>
						<input
							ref={fileInputRef}
							type="file"
							accept=".zip,application/zip"
							className="skills-file-input sr-only"
							tabIndex={-1}
							aria-hidden="true"
							onChange={handleInputChange}
						/>
					</div>

					{actionError && (
						<p
							className="skills-upload-error mt-2 text-sm text-destructive"
							role="alert"
						>
							{actionError}
						</p>
					)}
					{actionNotice && !actionError && (
						<p className="skills-upload-notice mt-2 text-sm text-muted-foreground">
							{actionNotice}
						</p>
					)}
				</div>

				<div className="skills-modal-body flex min-h-0 flex-1 flex-col overflow-hidden max-lg:min-h-96 max-lg:flex-none lg:flex-row">
					<div className="skills-modal-list min-h-0 flex-1 overflow-y-auto border-b border-border/60 px-6 py-4 lg:border-b-0 lg:border-r">
						{skillList.length > 0 && (
							<>
								<h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-muted-foreground">
									Default skills
								</h3>
								<ul className="skills-default-list mb-6 flex flex-col gap-1.5">
									{skillList.map((skill) => {
										const isDefault = defaultIds.includes(skill.id);
										const optionId = `skill-default-${skill.id}`;
										return (
											<li
												key={skill.id}
												className="skills-default-row flex flex-wrap items-center gap-2 rounded-md border border-border/60 bg-background/70 px-3 py-1.5"
											>
												<Checkbox
													id={optionId}
													checked={isDefault}
													disabled={skill.enabled === false}
													onCheckedChange={(checked) =>
														onDefaultSkillIdsChange(
															checked === true
																? [...defaultIds, skill.id]
																: defaultIds.filter((id) => id !== skill.id),
														)
													}
												/>
												<Label
													htmlFor={optionId}
													className="cursor-pointer text-xs font-medium text-foreground"
												>
													{skill.name || skill.id}
												</Label>
												{skill.enabled === false && (
													<Badge variant="subtle" className="text-[10px]">
														Disabled
													</Badge>
												)}
											</li>
										);
									})}
								</ul>
							</>
						)}

						<h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-muted-foreground">
							Installed ({skillList.length})
						</h3>
						{skillList.length === 0 ? (
							<p className="skills-list-empty rounded-lg border border-dashed border-border/60 bg-muted/20 px-4 py-6 text-sm text-muted-foreground">
								No skills installed yet.
							</p>
						) : (
							<ul className="skills-list flex flex-col gap-2">
								{skillList.map((skill) => (
									<SkillRow
										key={skill.id}
										skill={skill}
										isSelected={skill.id === selectedId}
										onSelect={setSelectedId}
										onToggleEnabled={handleToggleEnabled}
										onRequestDelete={setDeleteTarget}
										busy={isUploading}
									/>
								))}
							</ul>
						)}
					</div>

					<div className="skills-modal-detail min-h-0 flex-1 overflow-y-auto px-6 py-4">
						<SkillDetail
							skill={selectedSkill}
							markdown={selectedMarkdown}
							isLoadingDetail={isLoadingDetail}
						/>
					</div>
				</div>

				<div className="skills-modal-footer flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border/60 px-6 py-4">
					<p className="text-xs text-muted-foreground">
						Skill instructions are injected into Stage 1 and the chairman
						synthesis only.
					</p>
					<Button type="button" variant="outline" size="sm" onClick={onClose}>
						Close
					</Button>
				</div>

				<AlertDialog
					open={Boolean(deleteTarget)}
					onOpenChange={(open) => {
						if (!open) setDeleteTarget(null);
					}}
				>
					<AlertDialogContent>
						<AlertDialogHeader>
							<AlertDialogTitle>Delete skill?</AlertDialogTitle>
							<AlertDialogDescription>
								Delete &quot;{deleteTarget?.name || deleteTarget?.id}&quot;? Its
								bundle is removed from disk and this cannot be undone.
							</AlertDialogDescription>
						</AlertDialogHeader>
						<AlertDialogFooter>
							<AlertDialogCancel>Cancel</AlertDialogCancel>
							<AlertDialogAction
								onClick={handleConfirmDelete}
								className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
							>
								Delete
							</AlertDialogAction>
						</AlertDialogFooter>
					</AlertDialogContent>
				</AlertDialog>
			</DialogContent>
		</Dialog>
	);
}
