import { useState } from 'react';
import Markdown from './Markdown';
import ExportMarkdownButton from './ExportMarkdownButton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Badge } from './ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';

function deAnonymizeText(text, labelToModel) {
  if (!labelToModel || Object.keys(labelToModel).length === 0) {
    console.warn('labelToModel not available for de-anonymization');
    return text.replace(/Response [A-Za-z0-9]+/g, '[Model]');
  }

  let result = text;
  Object.entries(labelToModel).forEach(([label, model]) => {
    const modelShortName = model.split('/')[1] || model;
    result = result.replace(
      new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'),
      `**${modelShortName}**`
    );
  });
  return result;
}

export default function Stage2({
  rankings,
  labelToModel,
  aggregateRankings,
  conversationId,
  messageIndex,
}) {
  const [activeTab, setActiveTab] = useState('0');
  const [exportError, setExportError] = useState(null);

  if (!rankings || rankings.length === 0) {
    return null;
  }

  const activeIndex = Number(activeTab);
  const activeRanking = rankings[Number.isNaN(activeIndex) ? 0 : activeIndex] || rankings[0];
  const canExport = conversationId && messageIndex !== undefined;

  if (!activeRanking) {
    return null;
  }

  return (
    <Card className="my-6 border-border/60 bg-background/70">
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">Stage 2: Peer Rankings</CardTitle>
            <CardDescription>
              Each model evaluated all responses (anonymized as Response A, B, C, etc.) and provided rankings.
              Model names are shown in bold for readability.
            </CardDescription>
          </div>
          {canExport && (
            <ExportMarkdownButton
              conversationId={conversationId}
              messageIndex={messageIndex}
              className="gap-1.5"
              onError={setExportError}
            />
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {exportError && (
          <p className="text-sm text-destructive">{exportError}</p>
        )}
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="flex h-auto flex-wrap gap-2">
            {rankings.map((rank, index) => (
              <TabsTrigger key={rank.model || index} value={String(index)}>
                {rank.model.split('/')[1] || rank.model}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value={activeTab}>
            <div className="rounded-lg border border-border/60 bg-card p-4">
              <div className="mb-3 font-mono text-xs text-muted-foreground">
                {activeRanking.model}
              </div>
              <Markdown className="text-sm">
                {deAnonymizeText(activeRanking.ranking, labelToModel)}
              </Markdown>

              <div className="mt-4 border-t border-border/60 pt-4">
                <div className="flex flex-wrap items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Extracted ranking
                  {activeRanking.validation_status && (
                    <Badge
                      variant={
                        activeRanking.validation_status === 'valid' ? 'success' : 'warning'
                      }
                      className="text-[10px]"
                    >
                      {activeRanking.validation_status}
                    </Badge>
                  )}
                </div>
                {activeRanking.validation_error && (
                  <p className="mt-2 text-xs text-destructive">
                    {activeRanking.validation_error}
                  </p>
                )}
                {activeRanking.parsed_ranking && activeRanking.parsed_ranking.length > 0 ? (
                  <ol className="mt-3 space-y-1 pl-5 text-sm">
                    {activeRanking.parsed_ranking.map((label, i) => (
                      <li key={i} className="font-mono">
                        {labelToModel && labelToModel[label]
                          ? labelToModel[label].split('/')[1] || labelToModel[label]
                          : label}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="mt-2 text-sm text-muted-foreground italic">
                    No ranking could be extracted.
                  </p>
                )}
              </div>
            </div>
          </TabsContent>
        </Tabs>

        {aggregateRankings && aggregateRankings.length > 0 && (
          <Card className="border-border/60 bg-muted/40">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Aggregate Rankings (Street Cred)</CardTitle>
              <CardDescription>
                {aggregateRankings[0]?.win_percentage !== undefined
                  ? 'Pairwise comparison using Schulze method (higher win % is better).'
                  : 'Combined results across all peer evaluations (lower score is better).'}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ol className="space-y-2" aria-label="Model rankings by score">
                {aggregateRankings.map((agg, index) => {
                  const isPairwise = agg.win_percentage !== undefined;
                  return (
                    <li
                      key={agg.model}
                      className="flex flex-wrap items-center gap-3 rounded-lg border border-border/60 bg-background/80 p-3"
                    >
                      <Badge variant="secondary" aria-hidden="true">#{index + 1}</Badge>
                      <span className="flex-1 font-mono text-sm">
                        {agg.model.split('/')[1] || agg.model}
                      </span>
                      {isPairwise ? (
                        <>
                          <span className="text-xs text-muted-foreground">
                            Win: {(agg.win_percentage * 100).toFixed(0)}%
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {agg.pairwise_wins} wins
                          </span>
                        </>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          Avg: {agg.average_rank.toFixed(2)}
                        </span>
                      )}
                      <span className="text-xs text-muted-foreground">
                        {agg.rankings_count} votes
                      </span>
                    </li>
                  );
                })}
              </ol>
            </CardContent>
          </Card>
        )}
      </CardContent>
    </Card>
  );
}
