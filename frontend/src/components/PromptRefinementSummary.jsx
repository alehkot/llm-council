import { useEffect, useRef, useState } from 'react';
import { buildPromptRefinementMarkdown } from '../lib/promptRefinementMarkdown';
import { Badge } from './ui/badge';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import Markdown from './Markdown';

/**
 * Displays the finalized prompt refinement details.
 * Shows the used refined prompt only when it differs, while still surfacing
 * intent and clarifications for either final decision.
 */
export default function PromptRefinementSummary({ refinementData }) {
  const [copyState, setCopyState] = useState('idle');
  const resetTimeoutRef = useRef(null);

  useEffect(() => () => {
    if (resetTimeoutRef.current) {
      window.clearTimeout(resetTimeoutRef.current);
    }
  }, []);

  if (!refinementData) {
    return null;
  }

  const { decision, original_prompt, refined_prompt, intent, clarifying_answers, iterations } = refinementData;
  const usedPrompt = decision === 'use_refined'
    ? (refined_prompt || original_prompt)
    : (original_prompt || refined_prompt);
  const showsDistinctUsedPrompt = decision === 'use_refined'
    && original_prompt
    && usedPrompt
    && original_prompt !== usedPrompt;
  const hasClarifications = Array.isArray(clarifying_answers) && clarifying_answers.length > 0;
  const hasIntent = Boolean(intent);
  const markdownExport = buildPromptRefinementMarkdown(refinementData);
  const summaryLabel = decision === 'use_original' ? 'Original Prompt Kept' : 'Refined Prompt Used';

  // Don't show if there's nothing useful to summarize.
  if (!showsDistinctUsedPrompt && !hasClarifications && !hasIntent) {
    return null;
  }

  const handleCopyMarkdown = async () => {
    if (!markdownExport) {
      return;
    }

    if (resetTimeoutRef.current) {
      window.clearTimeout(resetTimeoutRef.current);
    }

    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error('Clipboard API is unavailable');
      }

      await navigator.clipboard.writeText(markdownExport);
      setCopyState('success');
    } catch (error) {
      console.error('Failed to copy prompt refinement markdown:', error);
      setCopyState('error');
    }

    resetTimeoutRef.current = window.setTimeout(() => {
      setCopyState('idle');
      resetTimeoutRef.current = null;
    }, 2000);
  };

  return (
    <div className="refinement-summary mt-3 space-y-2">
      <div className="refinement-summary-header flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant="outline"
            className="refinement-badge dark:bg-amber-950/50 dark:text-amber-400 dark:border-amber-800"
          >
            {summaryLabel}
          </Badge>
          {iterations > 1 && (
            <span className="refinement-iterations text-xs dark:text-muted-foreground">
              ({iterations} refinement rounds)
            </span>
          )}
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleCopyMarkdown}
          disabled={!markdownExport}
          className="refinement-copy-button h-7 px-2.5 text-xs dark:border-amber-700/50 dark:bg-amber-950/30 dark:hover:bg-amber-950/50"
        >
          {copyState === 'success'
            ? 'Exported'
            : copyState === 'error'
              ? 'Export failed'
              : 'Export Prompt'}
        </Button>
      </div>

      <Card className="refinement-card dark:border-amber-800/50 dark:bg-amber-950/20">
        <CardContent className="refinement-card-content p-3 space-y-3">
          {intent && (
            <div className="refinement-intent text-sm">
              <span className="refinement-intent-label font-medium dark:text-muted-foreground">
                Intent:{' '}
              </span>
              <span className="refinement-intent-value">{intent}</span>
            </div>
          )}

          {showsDistinctUsedPrompt && (
            <div className="refinement-prompt-section space-y-1">
              <div className="refinement-prompt-label text-xs font-semibold dark:text-amber-400 uppercase tracking-wide">
                Refined Prompt (Used)
              </div>
              <div className="refinement-prompt-content rounded-md border p-2 text-sm dark:border-amber-700/50 dark:bg-amber-950/30">
                <Markdown className="refinement-markdown dark:text-foreground">
                  {usedPrompt}
                </Markdown>
              </div>
            </div>
          )}

          {clarifying_answers && clarifying_answers.length > 0 && (
            <div className="refinement-clarifications space-y-1 pt-1 border-t dark:border-border/30">
              <div className="refinement-clarifications-label text-xs font-medium uppercase tracking-wide dark:text-muted-foreground">
                Clarifications Provided
              </div>
              <ul className="refinement-clarifications-list text-sm space-y-1">
                {clarifying_answers.map((qa, idx) => (
                  <li key={idx} className="refinement-clarification-item dark:text-muted-foreground">
                    <span className="refinement-question font-medium">{qa.question}</span>
                    <span className="refinement-separator mx-1">→</span>
                    <span className="refinement-answer text-foreground">{qa.answer}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
