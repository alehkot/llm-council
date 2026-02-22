import { Card, CardContent } from './ui/card';
import { Badge } from './ui/badge';
import Markdown from './Markdown';

/**
 * Displays the prompt refinement summary when a user chose to use the refined prompt.
 * Shows the refined prompt (only when it differs) without duplicating the original.
 */
export default function PromptRefinementSummary({ refinementData }) {
  if (!refinementData || refinementData.decision !== 'use_refined') {
    return null;
  }

  const { original_prompt, refined_prompt, intent, clarifying_answers, iterations } = refinementData;
  const hasPromptChange = original_prompt && refined_prompt && original_prompt !== refined_prompt;
  const hasClarifications = Array.isArray(clarifying_answers) && clarifying_answers.length > 0;
  const hasIntent = Boolean(intent);

  // Don't show if there's nothing useful to summarize.
  if (!hasPromptChange && !hasClarifications && !hasIntent) {
    return null;
  }

  return (
    <div className="refinement-summary mt-3 space-y-2">
      <div className="refinement-summary-header flex items-center gap-2">
        <Badge
          variant="outline"
          className="refinement-badge dark:bg-amber-950/50 dark:text-amber-400 dark:border-amber-800"
        >
          Refined Prompt Used
        </Badge>
        {iterations > 1 && (
          <span className="refinement-iterations text-xs dark:text-muted-foreground">
            ({iterations} refinement rounds)
          </span>
        )}
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

          {hasPromptChange && (
            <div className="refinement-prompt-section space-y-1">
              <div className="refinement-prompt-label text-xs font-semibold dark:text-amber-400 uppercase tracking-wide">
                Refined Prompt (Used)
              </div>
              <div className="refinement-prompt-content rounded-md border p-2 text-sm dark:border-amber-700/50 dark:bg-amber-950/30">
                <Markdown className="refinement-markdown dark:text-foreground">
                  {refined_prompt}
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
