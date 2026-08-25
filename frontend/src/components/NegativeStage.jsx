import Markdown from "./Markdown";
import { Badge } from "./ui/badge";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "./ui/card";
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "./ui/table";
import { cn } from "../lib/utils";

function formatLabel(label, labelToModel) {
	if (!label) return "";
	const model = labelToModel && labelToModel[label];
	const modelShort = model ? model.split("/")[1] || model : null;
	return modelShort ? `${label} / ${modelShort}` : label;
}

function formatScore(value) {
	if (value === null || value === undefined) return "n/a";
	const num = Number(value);
	if (Number.isNaN(num)) return "n/a";
	return Number.isInteger(num) ? num : num.toFixed(1);
}

function formatModelName(model) {
	if (!model) return "unknown";
	return model.split("/")[1] || model;
}

function AggregateGapScores({ aggregateGapScores }) {
	if (!aggregateGapScores || aggregateGapScores.length === 0) {
		return null;
	}

	return (
		<Card className="border-border/60 bg-background/80">
			<CardHeader className="pb-3">
				<CardTitle className="text-sm">Aggregate Gap Scores</CardTitle>
				<CardDescription>
					Each response was evaluated by all other models. Higher scores
					indicate better coverage of risks and edge cases.
				</CardDescription>
			</CardHeader>
			<CardContent className="min-w-0 space-y-3 overflow-x-auto">
				<Table className="min-w-[620px]">
					<TableHeader>
						<TableRow>
							{[
								"Rank",
								"Response",
								"Avg Score",
								"Std Dev",
								"Evaluations",
								"Self Bias",
							].map((label) => (
								<TableHead key={label}>{label}</TableHead>
							))}
						</TableRow>
					</TableHeader>
					<TableBody>
						{aggregateGapScores.map((item, index) => (
							<TableRow key={item.label}>
								<TableCell className="font-semibold text-foreground">
									#{index + 1}
								</TableCell>
								<TableCell>
									<div className="flex flex-col">
										<span className="text-sm font-semibold">{item.label}</span>
										<span className="text-xs text-muted-foreground">
											{formatModelName(item.model)}
										</span>
									</div>
								</TableCell>
								<TableCell className="font-semibold">
									{formatScore(item.avg_gap_score)}/10
								</TableCell>
								<TableCell className="text-muted-foreground">
									±{formatScore(item.std_gap_score)}
								</TableCell>
								<TableCell className="text-muted-foreground">
									{item.peer_evaluation_count}
								</TableCell>
								<TableCell
									className={cn(
										"font-medium",
										item.self_bias > 1
											? "text-destructive"
											: item.self_bias < -1
												? "text-emerald-700"
												: "text-foreground",
									)}
								>
									{item.self_bias !== null
										? item.self_bias > 0
											? `+${formatScore(item.self_bias)}`
											: formatScore(item.self_bias)
										: "n/a"}
								</TableCell>
							</TableRow>
						))}
					</TableBody>
				</Table>

				{aggregateGapScores.some((item) => item.gap_summaries?.length > 0) && (
					<div className="rounded-lg border border-border/60 bg-muted/40 p-3">
						<div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
							Key gaps identified
						</div>
						<div className="flex flex-col gap-2">
							{aggregateGapScores
								.flatMap((item) =>
									(item.gap_summaries || []).slice(0, 2).map((summary, idx) => (
										<div
											key={`${item.label}-${idx}`}
											className="text-xs text-foreground"
										>
											<span className="font-semibold text-emerald-700">
												{item.label}:
											</span>{" "}
											{summary.summary}
										</div>
									)),
								)
								.slice(0, 8)}
						</div>
					</div>
				)}
			</CardContent>
		</Card>
	);
}

function EvaluationCard({ evaluation, labelToModel }) {
	const scores = evaluation.scores || {};
	const summaries = evaluation.summaries || {};
	const peerScores = scores.peers || {};
	const peerSummaries = summaries.peers || {};
	const hasPeerScores = Object.keys(peerScores).length > 0;

	return (
		<Card className="border-border/60 bg-background/80">
			<CardHeader className="pb-3">
				<CardTitle className="text-sm">
					{formatModelName(evaluation.model)}
				</CardTitle>
				<Badge variant="subtle" className="mt-1 w-fit text-[10px]">
					Evaluator: {formatLabel(evaluation.self_label, labelToModel)}
				</Badge>
			</CardHeader>
			<CardContent className="space-y-3">
				<div className="rounded-lg border border-border/60 bg-muted/40 p-3">
					<div className="text-[11px] uppercase tracking-wide text-muted-foreground">
						Self score
					</div>
					<div className="text-lg font-semibold text-foreground">
						{formatScore(scores.self)}
					</div>
				</div>

				{summaries.self && (
					<div className="break-words text-sm text-foreground">
						<strong>Self gaps:</strong> {summaries.self}
					</div>
				)}

				{hasPeerScores && (
					<div className="space-y-2">
						<div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
							Peer evaluations
						</div>
						<div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-2">
							{Object.entries(peerScores).map(([label, score]) => (
								<div
									key={label}
									className="rounded-lg border border-border/60 bg-background/80 p-2"
								>
									<div className="text-[11px] text-muted-foreground">
										{formatLabel(label, labelToModel)}
									</div>
									<div className="text-sm font-semibold text-foreground">
										{formatScore(score)}/10
									</div>
									{peerSummaries[label] && (
										<div className="mt-1 break-words text-[11px] text-muted-foreground">
											{peerSummaries[label]}
										</div>
									)}
								</div>
							))}
						</div>
					</div>
				)}

				{!hasPeerScores && scores.peer !== undefined && (
					<>
						<div className="text-xs text-muted-foreground">
							Peer target: {formatLabel(evaluation.peer_label, labelToModel)}
						</div>
						<div className="rounded-lg border border-border/60 bg-muted/40 p-3">
							<div className="text-[11px] uppercase tracking-wide text-muted-foreground">
								Peer score
							</div>
							<div className="text-lg font-semibold text-foreground">
								{formatScore(scores.peer)}
							</div>
						</div>
						{summaries.peer && (
							<div className="break-words text-sm text-foreground">
								<strong>Peer gaps:</strong> {summaries.peer}
							</div>
						)}
					</>
				)}

				{evaluation.evaluation && (
					<div className="min-w-0 overflow-x-auto border-t border-border/60 pt-3">
						<Markdown className="text-sm">{evaluation.evaluation}</Markdown>
					</div>
				)}
			</CardContent>
		</Card>
	);
}

export default function NegativeStage({
	negativeContext,
	labelToModel,
	aggregateGapScores,
}) {
	if (!negativeContext) {
		return null;
	}

	const { enabled, prompt, evaluations } = negativeContext;

	if (!enabled && !prompt) {
		return null;
	}

	if (!enabled) {
		return (
			<Card className="my-6 border-border/60 bg-background/70">
				<CardHeader>
					<CardTitle className="text-base">Negative Mode</CardTitle>
					<CardDescription>
						Negative mode is disabled in backend config. Provide a negative
						prompt only when it is enabled.
					</CardDescription>
				</CardHeader>
				{prompt && (
					<CardContent>
						<CardDescription>
							Prompt provided (not used): {prompt}
						</CardDescription>
					</CardContent>
				)}
			</Card>
		);
	}

	return (
		<Card className="negative-stage my-6 border-orange-500/10 bg-orange-500/5">
			<CardHeader>
				<CardTitle className="text-base">
					Negative Mode: Gap-Finding Analysis
				</CardTitle>
				<CardDescription>
					Models critique answers using the inverse prompt and score themselves
					and a peer.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				<div className="rounded-lg border border-dashed border-orange-500/20 bg-background/50 p-3 text-sm">
					<div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-orange-600 dark:text-orange-400">
						Negative prompt in use
					</div>
					<div className="text-foreground">
						{prompt || "Generated automatically based on your question."}
					</div>
				</div>

				<AggregateGapScores aggregateGapScores={aggregateGapScores} />

				{evaluations && evaluations.length > 0 ? (
					<div className="space-y-3">
						<div className="text-sm font-semibold text-orange-600 dark:text-orange-400">
							Individual Evaluations
						</div>
						<div className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-3">
							{evaluations.map((evaluation, index) => (
								<EvaluationCard
									key={index}
									evaluation={evaluation}
									labelToModel={labelToModel}
								/>
							))}
						</div>
					</div>
				) : (
					<p className="text-sm text-muted-foreground">
						Awaiting negative evaluations…
					</p>
				)}
			</CardContent>
		</Card>
	);
}
