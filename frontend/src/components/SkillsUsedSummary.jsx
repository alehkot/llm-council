import { Badge } from './ui/badge';
import { Card, CardContent } from './ui/card';

const FALLBACK_NOTES = {
  router_disabled:
    'Reference selection is disabled — references were picked by keyword overlap.',
  router_failed:
    'The reference selector returned an unusable answer — references were picked by keyword overlap.',
  router_timeout:
    'The reference selector timed out — references were picked by keyword overlap.',
  router_error:
    'The reference selector failed — references were picked by keyword overlap.',
};

const SKILL_SELECTION_FALLBACK_NOTES = {
  router_disabled:
    'The skill selector is disabled — skills were picked by keyword overlap.',
  router_failed:
    'The skill selector returned an unusable answer — skills were picked by keyword overlap.',
  router_timeout:
    'The skill selector timed out — skills were picked by keyword overlap.',
  router_error:
    'The skill selector failed — skills were picked by keyword overlap.',
};

function formatChars(chars) {
  const value = Number(chars);
  if (!Number.isFinite(value) || value <= 0) return '0 chars';
  if (value < 1000) return `${value} chars`;
  return `${(value / 1000).toFixed(1)}k chars`;
}

function pluralize(count, singular, plural) {
  return `${count} ${count === 1 ? singular : plural}`;
}

/**
 * Read-only record of the Agent Skills a finished assistant message ran under.
 * Everything here comes from server-written `message.metadata.skills`.
 */
export default function SkillsUsedSummary({ metadata }) {
  const skills = metadata?.skills;
  if (!skills) {
    return null;
  }

  const requested = Array.isArray(skills.requested) ? skills.requested : [];
  const resolved = Array.isArray(skills.resolved) ? skills.resolved : [];
  const missing = Array.isArray(skills.missing) ? skills.missing : [];
  const selection = skills.reference_selection || {};
  const skillSelection = skills.skill_selection || {};
  const budget = skills.budget || {};
  const droppedFromSynthesis = Array.isArray(budget.stage3_dropped_references)
    ? budget.stage3_dropped_references
    : [];

  const isAuto = skillSelection.mode === 'auto'
    || skillSelection.mode === 'auto_resumed';

  const resolvedIds = new Set(resolved.map((entry) => entry?.id).filter(Boolean));
  const pendingIds = requested.filter(
    (id) => id && !resolvedIds.has(id) && !missing.includes(id)
  );

  if (
    resolved.length === 0
    && pendingIds.length === 0
    && missing.length === 0
    && !isAuto
  ) {
    return null;
  }

  const pickedCount = resolved.length + pendingIds.length;
  const candidateCount = Number(skillSelection.candidate_count) || 0;
  const autoModel = skillSelection.model ? ` via ${skillSelection.model}` : '';
  let autoSummary = null;
  if (isAuto) {
    if (pickedCount === 0) {
      autoSummary = candidateCount > 0
        ? `Auto-select matched none of the ${candidateCount} enabled skills — the council ran without skill context.`
        : 'Auto-select matched no skills — the council ran without skill context.';
    } else {
      autoSummary = candidateCount > 0
        ? `Auto-selected ${pickedCount} of ${candidateCount} enabled skills${autoModel}.`
        : `Auto-selected ${pluralize(pickedCount, 'skill', 'skills')}${autoModel}.`;
    }
  }

  const autoReasons = skillSelection.reasons || {};
  const autoReasonRows = isAuto
    ? resolved
        .map((entry) => ({ id: entry?.id, reason: autoReasons[entry?.id] }))
        .filter((row) => row.id && row.reason)
    : [];

  const skillFallbackNote = isAuto && skillSelection.fallback
    ? SKILL_SELECTION_FALLBACK_NOTES[skillSelection.fallback]
      || `Skill auto-selection fell back to keyword overlap (${skillSelection.fallback}).`
    : null;

  const referenceRows = [];
  let skippedCount = 0;
  let truncatedCount = 0;
  for (const entry of resolved) {
    if (entry?.truncated) truncatedCount += 1;
    if (Array.isArray(entry?.references_skipped)) {
      skippedCount += entry.references_skipped.length;
    }
    if (!Array.isArray(entry?.references)) continue;
    for (const reference of entry.references) {
      if (!reference?.path) continue;
      referenceRows.push({
        skillId: entry.id,
        path: reference.path,
        chars: reference.chars,
        reason: reference.reason,
      });
    }
  }

  const fallbackNote = selection.fallback
    ? FALLBACK_NOTES[selection.fallback]
      || `Reference selection fell back to keyword overlap (${selection.fallback}).`
    : null;

  return (
    <div className="skills-used-summary mt-3 space-y-2">
      <div className="skills-used-header flex flex-wrap items-center gap-2">
        <span className="skills-used-label text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Agent Skills
        </span>
        {isAuto && (
          <Badge
            variant="subtle"
            className="skills-used-badge skills-used-badge--auto font-mono text-[11px]"
          >
            auto
          </Badge>
        )}
        {resolved.map((entry) => (
          <Badge
            key={entry.id}
            variant="outline"
            className="skills-used-badge font-mono text-[11px]"
          >
            {entry.id}
            {entry.version ? ` v${entry.version}` : ''}
          </Badge>
        ))}
        {pendingIds.map((id) => (
          <Badge
            key={id}
            variant="subtle"
            className="skills-used-badge skills-used-badge--pending font-mono text-[11px]"
          >
            {id}
          </Badge>
        ))}
        {pickedCount > 0 && (
          <span className="skills-used-reference-count text-xs text-muted-foreground">
            {pluralize(referenceRows.length, 'reference inlined', 'references inlined')}
          </span>
        )}
      </div>

      {autoSummary && (
        <p className="skills-used-note skills-used-note--auto-summary text-xs text-muted-foreground">
          {autoSummary}
        </p>
      )}

      {autoReasonRows.length > 0 && (
        <Card className="skills-used-card skills-used-card--auto border-border/60 bg-muted/20">
          <CardContent className="skills-used-card-content space-y-1 p-3">
            {autoReasonRows.map((row) => (
              <div
                key={row.id}
                className="skills-used-auto-reason flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs"
              >
                <span className="skills-used-auto-reason-id font-mono text-foreground">
                  {row.id}
                </span>
                <span className="skills-used-auto-reason-text text-muted-foreground">
                  — {row.reason}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {skillFallbackNote && (
        <p className="skills-used-note skills-used-note--skill-fallback rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-900/90 dark:text-amber-100">
          {skillFallbackNote}
        </p>
      )}

      {referenceRows.length > 0 && (
        <Card className="skills-used-card border-border/60 bg-muted/20">
          <CardContent className="skills-used-card-content space-y-1 p-3">
            {referenceRows.map((row) => (
              <div
                key={`${row.skillId}/${row.path}`}
                className="skills-used-reference flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs"
              >
                <span className="skills-used-reference-path font-mono text-foreground">
                  {row.skillId}/{row.path}
                </span>
                <span className="skills-used-reference-size text-muted-foreground">
                  {formatChars(row.chars)}
                </span>
                {row.reason && (
                  <span className="skills-used-reference-reason text-muted-foreground">
                    — {row.reason}
                  </span>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {fallbackNote && (
        <p className="skills-used-note skills-used-note--fallback rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-900/90 dark:text-amber-100">
          {fallbackNote}
        </p>
      )}

      {skills.drift && (
        <p className="skills-used-note skills-used-note--drift rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-900/90 dark:text-amber-100">
          A selected skill changed after this consultation started — the context was
          re-rendered from the current version.
        </p>
      )}

      {missing.length > 0 && (
        <p className="skills-used-note skills-used-note--missing rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-900/90 dark:text-amber-100">
          Unavailable when this ran: {missing.join(', ')}
        </p>
      )}

      {(droppedFromSynthesis.length > 0 || skippedCount > 0 || truncatedCount > 0) && (
        <p className="skills-used-note skills-used-note--budget text-xs text-muted-foreground">
          {droppedFromSynthesis.length > 0 && (
            <span className="skills-used-note-dropped">
              {pluralize(droppedFromSynthesis.length, 'reference', 'references')} omitted
              from synthesis (budget).{' '}
            </span>
          )}
          {skippedCount > 0 && (
            <span className="skills-used-note-skipped">
              {pluralize(skippedCount, 'reference', 'references')} not selected.{' '}
            </span>
          )}
          {truncatedCount > 0 && (
            <span className="skills-used-note-truncated">
              {pluralize(truncatedCount, 'skill was', 'skills were')} truncated to fit the
              context budget.
            </span>
          )}
        </p>
      )}
    </div>
  );
}
