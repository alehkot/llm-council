const DEFAULT_PROMPT_PLACEHOLDER = "_No prompt captured._";
const DEFAULT_ANSWER_PLACEHOLDER = "_No answer captured._";

function normalizeText(value) {
	return typeof value === "string" ? value.trim() : "";
}

function getCodeFence(content = "") {
	const matches = content.match(/`+/g) || [];
	const longestMatch = matches.reduce(
		(max, match) => Math.max(max, match.length),
		0,
	);
	return "`".repeat(Math.max(3, longestMatch + 1));
}

function appendPromptBlock(
	lines,
	content,
	emptyPlaceholder = DEFAULT_PROMPT_PLACEHOLDER,
) {
	const text = normalizeText(content);
	if (!text) {
		lines.push(emptyPlaceholder, "");
		return;
	}

	const fence = getCodeFence(text);
	lines.push(fence, text, fence, "");
}

function appendIndentedCodeBlock(
	lines,
	content,
	emptyPlaceholder = DEFAULT_ANSWER_PLACEHOLDER,
) {
	const text = normalizeText(content);
	if (!text) {
		lines.push(`   ${emptyPlaceholder}`);
		return;
	}

	const fence = getCodeFence(text);
	lines.push(`   ${fence}`);
	text.split("\n").forEach((line) => {
		lines.push(line ? `   ${line}` : "   ");
	});
	lines.push(`   ${fence}`);
}

export function buildPromptRefinementMarkdown(refinementData) {
	if (!refinementData) {
		return "";
	}

	const originalPrompt = normalizeText(refinementData.original_prompt);
	const refinedPrompt = normalizeText(refinementData.refined_prompt);
	const decision = normalizeText(refinementData.decision);
	const clarifyingAnswers = Array.isArray(refinementData.clarifying_answers)
		? refinementData.clarifying_answers.filter(
				(item) => normalizeText(item?.question) || normalizeText(item?.answer),
			)
		: [];

	let usedPrompt = originalPrompt;
	if (decision === "use_refined") {
		usedPrompt = refinedPrompt || originalPrompt;
	} else if (!usedPrompt) {
		usedPrompt = refinedPrompt;
	}

	const lines = [];
	appendPromptBlock(lines, usedPrompt);

	if (decision === "use_refined" && clarifyingAnswers.length > 0) {
		lines.push("### Clarifications Provided", "");
		clarifyingAnswers.forEach((item, index) => {
			const question = normalizeText(item.question) || `Question ${index + 1}`;
			lines.push(`${index + 1}. **${question}**`, "");
			appendIndentedCodeBlock(lines, item.answer);
			lines.push("");
		});
	}

	return lines.join("\n").trim();
}
