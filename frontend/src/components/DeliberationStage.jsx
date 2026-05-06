import { useState } from 'react';
import Markdown from './Markdown';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from './ui/accordion';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';

function formatModelName(model) {
  if (!model) return 'unknown';
  return model.split('/')[1] || model;
}

function buildMarkdownContent(revision) {
  return revision.revised || '';
}

export default function DeliberationStage({ deliberation, onStartSideChat }) {
  const [expandedItems, setExpandedItems] = useState([]);
  const [copiedIndex, setCopiedIndex] = useState(null);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerRevision, setViewerRevision] = useState(null);

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

  const handleCopyMarkdown = async (revision, index) => {
    const markdown = buildMarkdownContent(revision);
    try {
      await navigator.clipboard.writeText(markdown);
      setCopiedIndex(index);
      setTimeout(() => setCopiedIndex(null), 2000);
    } catch (err) {
      console.error('Failed to copy to clipboard:', err);
    }
  };

  const handleOpenViewer = (revision) => {
    setViewerRevision(revision);
    setViewerOpen(true);
  };

  return (
    <>
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
                    <div className="mb-4 flex flex-wrap justify-end gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => handleCopyMarkdown(revision, index)}
                        aria-label={`Copy ${formatModelName(revision.model)} deliberation response as markdown`}
                      >
                        {copiedIndex === index ? (
                          <span className="flex items-center gap-1.5">
                            <svg aria-hidden="true" className="h-3.5 w-3.5 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                            Copied
                          </span>
                        ) : (
                          <span className="flex items-center gap-1.5">
                            <svg aria-hidden="true" className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                            </svg>
                            Copy Markdown
                          </span>
                        )}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => handleOpenViewer(revision)}
                        aria-label={`View ${formatModelName(revision.model)} deliberation response in full screen`}
                      >
                        <span className="flex items-center gap-1.5">
                          <svg aria-hidden="true" className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M15 3h6v6" />
                            <path d="M9 21H3v-6" />
                            <path d="M21 3l-7 7" />
                            <path d="M3 21l7-7" />
                          </svg>
                          View Full
                        </span>
                      </Button>
                      {onStartSideChat && (
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
                      )}
                    </div>
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

      {/* Full-screen reader modal for a single deliberation response */}
      <Dialog open={viewerOpen} onOpenChange={setViewerOpen}>
        <DialogContent className="flex h-[min(90vh,60rem)] max-w-4xl flex-col overflow-hidden p-0">
          <DialogHeader className="border-b border-border/60 px-6 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="space-y-1">
                <DialogTitle>
                  Deliberation · {viewerRevision ? formatModelName(viewerRevision.model) : ''}
                </DialogTitle>
                <DialogDescription>
                  {viewerRevision && viewerRevision.original !== viewerRevision.revised
                    ? 'This model revised its response after reviewing peer feedback.'
                    : 'This model kept its original response unchanged.'}
                </DialogDescription>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {viewerRevision?.label && (
                  <Badge variant="subtle" className="text-[10px]">
                    {viewerRevision.label}
                  </Badge>
                )}
                {viewerRevision?.ranking_before && (
                  <Badge variant="outline" className="text-[10px]">
                    Rank #{viewerRevision.ranking_before.toFixed(1)}
                  </Badge>
                )}
                <Badge
                  variant={viewerRevision && viewerRevision.original !== viewerRevision.revised ? 'success' : 'subtle'}
                  className="text-[10px]"
                >
                  {viewerRevision && viewerRevision.original !== viewerRevision.revised ? 'Revised' : 'Unchanged'}
                </Badge>
              </div>
            </div>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            {viewerRevision && viewerRevision.original !== viewerRevision.revised ? (
              <div className="space-y-6">
                <div>
                  <h3 className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-destructive">
                    Original Response
                  </h3>
                  <div className="rounded-lg border border-border/60 bg-muted/30 p-4">
                    <Markdown className="text-sm">{viewerRevision.original}</Markdown>
                  </div>
                </div>
                <div>
                  <h3 className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-emerald-700 dark:text-emerald-400">
                    Revised Response
                  </h3>
                  <div className="rounded-lg border border-border/60 bg-background p-4">
                    <Markdown className="text-sm">{viewerRevision.revised}</Markdown>
                  </div>
                </div>
              </div>
            ) : viewerRevision ? (
              <div>
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  Response (Unchanged)
                </h3>
                <div className="rounded-lg border border-border/60 bg-background p-4">
                  <Markdown className="text-sm">{viewerRevision.revised}</Markdown>
                </div>
              </div>
            ) : null}
          </div>
          <div className="border-t border-border/60 px-6 py-3 flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => viewerRevision && handleCopyMarkdown(viewerRevision, -1)}
            >
              {copiedIndex === -1 ? (
                <span className="flex items-center gap-1.5">
                  <svg aria-hidden="true" className="h-3.5 w-3.5 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                  Copied
                </span>
              ) : (
                <span className="flex items-center gap-1.5">
                  <svg aria-hidden="true" className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                  </svg>
                  Copy Markdown
                </span>
              )}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setViewerOpen(false)}
            >
              Close
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
