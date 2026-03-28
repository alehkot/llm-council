import { useState } from 'react';
import Markdown from './Markdown';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from './ui/accordion';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';

function formatModelName(model) {
  if (!model) return 'unknown';
  return model.split('/')[1] || model;
}

export default function DeliberationStage({ deliberation, onStartSideChat }) {
  const [expandedItems, setExpandedItems] = useState([]);

  if (!deliberation || !deliberation.enabled) {
    return null;
  }

  const results = deliberation.results || [];

  if (results.length === 0) {
    return null;
  }

  const revisedCount = results.filter((revision) => revision.original !== revision.revised).length;

  const expandAll = () => {
    setExpandedItems(results.map((_, index) => String(index)));
  };

  const collapseAll = () => {
    setExpandedItems([]);
  };

  return (
    <Card className="deliberation-stage my-6 border-blue-500/10 bg-blue-500/5">
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="text-base">Deliberation Round</CardTitle>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={expandAll}
              disabled={expandedItems.length === results.length}
              aria-label={`Expand all ${results.length} model revisions`}
            >
              Expand All
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={collapseAll}
              disabled={expandedItems.length === 0}
              aria-label="Collapse all model revisions"
            >
              Collapse All
            </Button>
          </div>
        </div>
        <div className="rounded-lg border border-dashed border-blue-500/20 bg-background/50 p-3 text-sm text-muted-foreground">
          Models received peer ranking feedback and gap analysis.{' '}
          <strong className="text-foreground">{revisedCount} of {results.length}</strong> models revised their responses.
        </div>
      </CardHeader>
      <CardContent>
        <Accordion
          type="multiple"
          value={expandedItems}
          onValueChange={setExpandedItems}
          className="space-y-3"
        >
          {results.map((revision, index) => {
            const hasChanges = revision.original !== revision.revised;
            return (
              <AccordionItem key={index} value={String(index)} className="border-blue-500/10">
                <AccordionTrigger className="text-left">
                  <div className="flex flex-1 flex-col gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-blue-600 dark:text-blue-400">
                        {formatModelName(revision.model)}
                      </span>
                      <Badge variant="subtle" className="text-[10px]">
                        {revision.label}
                      </Badge>
                      {revision.ranking_before && (
                        <Badge variant="outline" className="text-[10px]">
                          Rank #{revision.ranking_before.toFixed(1)}
                        </Badge>
                      )}
                      <Badge variant={hasChanges ? 'success' : 'subtle'} className="text-[10px]">
                        {hasChanges ? 'Revised' : 'Unchanged'}
                      </Badge>
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {hasChanges
                        ? 'Reviewed feedback and updated the answer.'
                        : 'Kept original response.'}
                    </span>
                  </div>
                </AccordionTrigger>
                <AccordionContent>
                  {onStartSideChat && (
                    <div className="mb-4 flex justify-end">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => onStartSideChat({
                          sourceType: 'deliberation',
                          sourceModel: revision.model,
                          sourceTitle: `Deliberation · ${formatModelName(revision.model)}`,
                          sourcePreview: revision.revised,
                        })}
                      >
                        Side Chat
                      </Button>
                    </div>
                  )}
                  {hasChanges ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      <Card className="border-border/60 bg-background/80">
                        <CardHeader className="pb-2">
                          <CardTitle className="text-xs uppercase tracking-wide text-destructive">
                            Original response
                          </CardTitle>
                        </CardHeader>
                        <CardContent>
                          <Markdown className="text-sm">{revision.original}</Markdown>
                        </CardContent>
                      </Card>
                      <Card className="border-border/60 bg-background/80">
                        <CardHeader className="pb-2">
                          <CardTitle className="text-xs uppercase tracking-wide text-emerald-700 dark:text-emerald-400">
                            Revised response
                          </CardTitle>
                        </CardHeader>
                        <CardContent>
                          <Markdown className="text-sm">{revision.revised}</Markdown>
                        </CardContent>
                      </Card>
                    </div>
                  ) : (
                    <Card className="border-border/60 bg-muted/40">
                      <CardContent className="space-y-3">
                        <p className="text-sm italic text-muted-foreground">
                          Model kept its original response with no significant changes.
                        </p>
                        <Markdown className="text-sm">{revision.revised}</Markdown>
                      </CardContent>
                    </Card>
                  )}
                </AccordionContent>
              </AccordionItem>
            );
          })}
        </Accordion>
      </CardContent>
    </Card>
  );
}
