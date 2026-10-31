import { useCallback, useState } from "react";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Checkbox } from "./ui/checkbox";
import { Label } from "./ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "./ui/select";
import { Textarea } from "./ui/textarea";

function SelectField({
	field,
	value,
	onChange,
	disabled = false,
	description,
	optionDisabled,
}) {
	const selectId = `select-${field.id}`;
	const resolvedDescription = description ?? field.description;
	const exampleLabel = field.options?.[0]?.label;
	const placeholder =
		field.placeholder ??
		(exampleLabel
			? `Select an option… e.g., ${exampleLabel}`
			: "Select an option…");
	return (
		<div className="flex flex-col gap-2">
			<Label htmlFor={selectId}>{field.label}</Label>
			{resolvedDescription && (
				<p
					id={`${selectId}-description`}
					className="text-xs text-muted-foreground whitespace-pre-wrap"
				>
					{resolvedDescription}
				</p>
			)}
			<Select
				value={value ?? field.default ?? ""}
				onValueChange={onChange}
				disabled={disabled}
			>
				<SelectTrigger
					id={selectId}
					aria-describedby={
						resolvedDescription ? `${selectId}-description` : undefined
					}
				>
					<SelectValue placeholder={placeholder} />
				</SelectTrigger>
				<SelectContent>
					{field.options.map((option) => (
						<SelectItem
							key={option.value}
							value={option.value}
							disabled={optionDisabled ? optionDisabled(option) : false}
						>
							{option.label}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
		</div>
	);
}

function MultiSelectField({ field, value, onChange }) {
	const selectedValues = value ?? field.default ?? [];

	const toggleOption = (optionValue, isChecked) => {
		if (isChecked) {
			onChange([...new Set([...selectedValues, optionValue])]);
		} else {
			onChange(selectedValues.filter((v) => v !== optionValue));
		}
	};

	return (
		<div className="flex flex-col gap-2">
			<Label>{field.label}</Label>
			{field.description && (
				<p className="text-xs text-muted-foreground whitespace-pre-wrap">
					{field.description}
				</p>
			)}
			<div className="grid gap-2">
				{field.options.map((option) => {
					const id = `${field.id}-${option.value}`;
					return (
						<div
							key={option.value}
							className="flex items-start gap-3 rounded-lg border border-border/60 bg-background/80 p-3"
						>
							<Checkbox
								id={id}
								checked={selectedValues.includes(option.value)}
								onCheckedChange={(checked) =>
									toggleOption(option.value, checked === true)
								}
							/>
							<Label htmlFor={id} className="flex-1 cursor-pointer">
								<div className="text-sm font-medium text-foreground">
									{option.label}
								</div>
								{option.description && (
									<div className="text-xs text-muted-foreground">
										{option.description}
									</div>
								)}
							</Label>
						</div>
					);
				})}
			</div>
		</div>
	);
}

function TextField({ field, value, onChange, highlight = false }) {
	const textareaId = `textarea-${field.id}`;
	const highlightClasses = highlight
		? "border-amber-500/60 bg-amber-500/10 focus-visible:ring-amber-400/40"
		: "";
	const placeholder =
		field.placeholder ??
		"Add details… e.g., constraints, priorities, or outcomes";
	return (
		<div className="flex flex-col gap-2">
			<Label
				htmlFor={textareaId}
				className={highlight ? "text-amber-900 dark:text-amber-100" : undefined}
			>
				{field.label}
			</Label>
			{field.description && (
				<p
					id={`${textareaId}-description`}
					className="text-xs text-muted-foreground whitespace-pre-wrap"
				>
					{field.description}
				</p>
			)}
			<Textarea
				id={textareaId}
				name={field.id}
				className={`min-h-[90px] resize-y ${highlightClasses}`}
				value={value ?? field.default ?? ""}
				onChange={(e) => onChange(e.target.value)}
				rows={3}
				placeholder={placeholder}
				autoComplete="off"
				aria-describedby={
					field.description ? `${textareaId}-description` : undefined
				}
			/>
		</div>
	);
}

function CheckboxField({ field, value, onChange }) {
	const id = `${field.id}-checkbox`;
	return (
		<div className="flex items-start gap-3">
			<Checkbox
				id={id}
				checked={value ?? field.default ?? false}
				onCheckedChange={(checked) => onChange(checked === true)}
			/>
			<Label htmlFor={id} className="cursor-pointer">
				<div className="text-sm font-medium text-foreground">{field.label}</div>
				{field.description && (
					<p className="text-xs text-muted-foreground whitespace-pre-wrap">
						{field.description}
					</p>
				)}
			</Label>
		</div>
	);
}

export default function InteractivePrompt({ prompt, onSubmit, onSkip }) {
	const isPromptRefinement = prompt.prompt_type === "prompt_refinement";
	const isRefinementLocked = useCallback(
		(values) => {
			if (!isPromptRefinement) return false;
			const hasClarifyingAnswers = Object.entries(values).some(
				([key, value]) => {
					if (!key.startsWith("clarifying_answer_")) return false;
					return String(value ?? "").trim().length > 0;
				},
			);
			const hasAdditionalInsights =
				String(values.additional_insights ?? "").trim().length > 0;
			return hasClarifyingAnswers || hasAdditionalInsights;
		},
		[isPromptRefinement],
	);
	const applyRefinementLock = useCallback(
		(values) => {
			if (!isRefinementLocked(values)) return values;
			if (values.refinement_action === "refine_again") return values;
			return {
				...values,
				refinement_action: "refine_again",
			};
		},
		[isRefinementLocked],
	);

	const buildInitialResponses = useCallback(() => {
		const initial = {};
		for (const field of prompt.fields || []) {
			initial[field.id] = field.default;
		}
		return applyRefinementLock(initial);
	}, [applyRefinementLock, prompt.fields]);
	const [responses, setResponses] = useState(buildInitialResponses);
	const refinementActionLocked = isRefinementLocked(responses);

	const handleFieldChange = (fieldId, value) => {
		setResponses((prev) => applyRefinementLock({ ...prev, [fieldId]: value }));
	};

	const handleSubmit = (e) => {
		e.preventDefault();
		onSubmit(responses);
	};

	const handleSkip = () => {
		if (prompt.skip_allowed) {
			onSkip();
		}
	};

	const renderField = (field) => {
		const value = responses[field.id];

		switch (field.type) {
			case "select":
				if (isPromptRefinement && field.id === "refinement_action") {
					const baseDescription = field.description || "";
					const lockNotice =
						"Because you entered clarifying answers or additional insights, " +
						'Next step is locked to "Refine with more details". Clear those fields to unlock.';
					const resolvedDescription = baseDescription;
					return (
						<div className="flex flex-col gap-2" key={field.id}>
							<SelectField
								field={field}
								value={value}
								onChange={(v) => handleFieldChange(field.id, v)}
								description={resolvedDescription}
								optionDisabled={(option) =>
									refinementActionLocked && option.value !== "refine_again"
								}
							/>
							{refinementActionLocked && (
								<div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-900/90 shadow-sm dark:text-amber-100">
									<span className="font-semibold">Next step locked.</span>{" "}
									{lockNotice}
								</div>
							)}
						</div>
					);
				}
				return (
					<SelectField
						key={field.id}
						field={field}
						value={value}
						onChange={(v) => handleFieldChange(field.id, v)}
					/>
				);
			case "multi_select":
				return (
					<MultiSelectField
						key={field.id}
						field={field}
						value={value}
						onChange={(v) => handleFieldChange(field.id, v)}
					/>
				);
			case "text":
				if (isPromptRefinement) {
					const highlight =
						(field.id.startsWith("clarifying_answer_") ||
							field.id === "additional_insights") &&
						String(value ?? "").trim().length > 0;
					return (
						<TextField
							key={field.id}
							field={field}
							value={value}
							onChange={(v) => handleFieldChange(field.id, v)}
							highlight={highlight}
						/>
					);
				}
				return (
					<TextField
						key={field.id}
						field={field}
						value={value}
						onChange={(v) => handleFieldChange(field.id, v)}
					/>
				);
			case "checkbox":
				return (
					<CheckboxField
						key={field.id}
						field={field}
						value={value}
						onChange={(v) => handleFieldChange(field.id, v)}
					/>
				);
			default:
				return null;
		}
	};

	return (
		<Card className="glass ring-1 ring-foreground/5 shadow-md">
			<CardHeader className="pb-3">
				<div className="flex items-center gap-3">
					<div className="text-2xl" aria-hidden="true">
						💬
					</div>
					<div>
						<CardTitle className="text-xl">{prompt.title}</CardTitle>
						<p className="text-sm text-muted-foreground whitespace-pre-wrap">
							{prompt.description}
						</p>
					</div>
				</div>
			</CardHeader>
			<CardContent>
				<form onSubmit={handleSubmit} className="space-y-5">
					{(prompt.fields || []).map(renderField)}
					<div className="flex flex-wrap justify-end gap-2">
						{prompt.skip_allowed && (
							<Button type="button" variant="outline" onClick={handleSkip}>
								Skip (Use Defaults)
							</Button>
						)}
						<Button type="submit">Continue</Button>
					</div>
				</form>
			</CardContent>
		</Card>
	);
}
