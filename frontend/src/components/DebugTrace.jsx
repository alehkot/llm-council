import { useMemo, useState } from 'react';
import { cn } from '../lib/utils';

const STAGE_LABELS = {
  stage1: 'Stage 1',
  stage2: 'Stage 2',
  stage3: 'Stage 3',
  negative: 'Negative Mode',
  negative_prompt: 'Negative Prompt',
  negative_eval: 'Negative Eval',
  title: 'Title',
  prompt_refinement: 'Prompt Refinement',
  skill_references: 'Skill References',
};

const KIND_LABELS = {
  flow_start: 'Flow Start',
  flow_complete: 'Flow Complete',
  stage_start: 'Stage Start',
  stage_complete: 'Stage Complete',
  llm_call: 'LLM Call',
  prompt_refinement_suggestion: 'Refinement Suggestion',
  prompt_refinement_response: 'Refinement Response',
  prompt_refinement_timeout: 'Refinement Timeout',
  prompt_refinement_skipped: 'Refinement Skipped',
  prompt_finalized: 'Final Prompt',
  skills_resolved: 'Skills Resolved',
  error: 'Error',
};

const OMITTED_EVENT_FIELDS = new Set([
  'id',
  'kind',
  'timestamp',
  'stage',
  'model',
  'request',
  'response',
  'timing',
  'error',
  'context',
  '_order',
  '_time',
]);

const formatDuration = (ms) => {
  if (ms === null || ms === undefined) return 'n/a';
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${ms.toFixed(0)}ms`;
};

const formatTime = (value) => {
  if (!value) return 'n/a';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString();
};

const safeStringify = (value) => {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return JSON.stringify({ error: 'Unable to serialize trace payload.' }, null, 2);
  }
};

const extractExtraPayload = (event) => {
  if (!event) return {};
  return Object.keys(event).reduce((accumulator, key) => {
    if (!OMITTED_EVENT_FIELDS.has(key) && event[key] !== undefined) {
      accumulator[key] = event[key];
    }
    return accumulator;
  }, {});
};

export default function DebugTrace({ trace }) {
  const [isOpen, setIsOpen] = useState(false);

  const events = useMemo(() => {
    if (!trace?.events?.length) return [];
    return trace.events
      .map((event, index) => {
        const time = event?.timing?.started_at || event?.timestamp || trace.started_at;
        return { ...event, _order: index, _time: time };
      })
      .sort((a, b) => {
        const timeA = Date.parse(a._time || '') || 0;
        const timeB = Date.parse(b._time || '') || 0;
        if (timeA === timeB) return a._order - b._order;
        return timeA - timeB;
      });
  }, [trace]);

  const llmCalls = useMemo(
    () => events.filter((event) => event.kind === 'llm_call'),
    [events]
  );

  const modelsTouched = useMemo(() => {
    const names = new Set(llmCalls.map((event) => event.model).filter(Boolean));
    return names.size;
  }, [llmCalls]);

  const errorCount = useMemo(
    () => llmCalls.filter((event) => event.error).length,
    [llmCalls]
  );

  const totalDurationMs = useMemo(() => {
    if (!trace?.started_at || !trace?.ended_at) return null;
    const start = Date.parse(trace.started_at);
    const end = Date.parse(trace.ended_at);
    if (Number.isNaN(start) || Number.isNaN(end)) return null;
    return Math.max(0, end - start);
  }, [trace]);

  if (!trace) {
    return null;
  }

  return (
    <div className="debug-trace relative mt-5 overflow-hidden rounded-xl border border-border/50 glass animate-[trace-rise_0.55s_ease] motion-reduce:animate-none">
      <button
        type="button"
        className="debug-trace-header relative z-10 flex w-full cursor-pointer items-center justify-between gap-3 border-0 bg-transparent px-5 py-4 text-left"
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
        aria-controls="debug-trace-content"
      >
        <div className="flex flex-col gap-1">
          <span className="text-lg font-bold tracking-tight">Debug Trace</span>
          <span className="text-xs uppercase tracking-widest text-muted-foreground">
            Flow {trace.flow_id?.slice(0, 8) || 'n/a'} - {llmCalls.length} calls
          </span>
        </div>
        <div className="flex items-center gap-3 text-xs font-semibold text-muted-foreground">
          <span>{modelsTouched} models</span>
          <span>{formatDuration(totalDurationMs)}</span>
          {errorCount > 0 && (
            <span className="inline-flex items-center rounded-full bg-destructive/15 px-2.5 py-1 leading-none text-destructive">
              {errorCount} errors
            </span>
          )}
        </div>
      </button>

      {isOpen && (
        <div id="debug-trace-content" className="debug-trace-content relative z-10 px-5 pb-6">
          <div className="debug-trace-summary grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-4 rounded-xl border border-dashed border-border bg-card/80 p-4">
            <div>
              <div className="text-xs uppercase tracking-widest text-muted-foreground">Started</div>
              <div className="text-lg font-semibold text-foreground">
                {formatTime(trace.started_at)}
              </div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-widest text-muted-foreground">Completed</div>
              <div className="text-lg font-semibold text-foreground">
                {formatTime(trace.ended_at)}
              </div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-widest text-muted-foreground">Total Duration</div>
              <div className="text-lg font-semibold text-foreground">
                {formatDuration(totalDurationMs)}
              </div>
            </div>
          </div>

          <div className="debug-trace-events mt-4 flex flex-col gap-3">
            {events.map((event, index) => {
              const stageLabel = STAGE_LABELS[event.stage] || event.stage || 'Flow';
              const kindLabel = KIND_LABELS[event.kind] || event.kind;
              const duration = event.timing?.duration_ms;
              const status = event.error ? 'Error' : 'OK';
              const detailRequest = event.request || null;
              const detailResponse = event.response || null;
              const extraPayload = extractExtraPayload(event);
              const detailContext = {
                ...(event.context || {}),
                ...extraPayload,
              };
              const hasContext = Object.keys(detailContext).length > 0;
              return (
                <details
                  key={event.id || `${event.kind}-${index}`}
                  className={cn(
                    'debug-trace-event rounded-xl border bg-card/90 py-1 animate-[trace-fade_0.35s_ease_both] motion-reduce:animate-none',
                    event.error
                      ? 'border-destructive/40 shadow-md shadow-destructive/10'
                      : 'border-border'
                  )}
                  style={{ animationDelay: `${index * 45}ms` }}
                  defaultOpen={Boolean(event.error)}
                >
                  <summary className="cursor-pointer list-none px-4 py-3 [&::-webkit-details-marker]:hidden [&::marker]:hidden">
                    <div className="flex items-center justify-between gap-4">
                      <div className="flex flex-wrap items-center gap-2.5 text-sm font-semibold">
                        <span className="rounded-full bg-accent/50 px-2.5 py-1 text-xs uppercase tracking-wide text-accent-foreground">
                          {kindLabel}
                        </span>
                        <span className="text-xs font-semibold text-muted-foreground">
                          {stageLabel}
                        </span>
                        {event.model && (
                          <span className="rounded-full bg-primary/15 px-2.5 py-1 text-xs text-foreground">
                            {event.model}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 text-xs text-muted-foreground">
                        {event.kind === 'llm_call' && (
                          <span className="font-bold text-primary">
                            {formatDuration(duration)}
                          </span>
                        )}
                        <span
                          className={cn(
                            'rounded-full px-2.5 py-1 font-bold',
                            event.error
                              ? 'bg-destructive/15 text-destructive'
                              : 'bg-accent/50 text-accent-foreground'
                          )}
                        >
                          {event.kind === 'llm_call' ? status : formatTime(event.timestamp)}
                        </span>
                      </div>
                    </div>
                  </summary>
                  <div className="flex flex-col gap-3 px-4 pb-4">
                    {event.error && (
                      <div className="rounded-lg border-l-4 border-l-destructive/60 bg-destructive/10 p-3">
                        <div className="mb-1.5 font-bold text-destructive">Error</div>
                        <div className="text-sm text-destructive/80">{event.error}</div>
                      </div>
                    )}
                    {hasContext && (
                      <div className="overflow-hidden rounded-xl border border-border/70 bg-muted/50 p-3">
                        <div className="mb-2 text-xs font-bold uppercase tracking-widest text-muted-foreground">
                          Context
                        </div>
                        <pre className="m-0 whitespace-pre-wrap break-all font-mono text-xs text-foreground">
                          {safeStringify(detailContext)}
                        </pre>
                      </div>
                    )}
                    {(detailRequest || detailResponse) && (
                      <div className="grid min-w-0 grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-3">
                        <div className="min-w-0 overflow-hidden rounded-xl border border-border/70 bg-muted/50 p-3">
                          <div className="mb-2 text-xs font-bold uppercase tracking-widest text-muted-foreground">
                            Request
                          </div>
                          <pre className="m-0 whitespace-pre-wrap break-all font-mono text-xs text-foreground">
                            {safeStringify(detailRequest)}
                          </pre>
                        </div>
                        <div className="min-w-0 overflow-hidden rounded-xl border border-border/70 bg-muted/50 p-3">
                          <div className="mb-2 text-xs font-bold uppercase tracking-widest text-muted-foreground">
                            Response
                          </div>
                          <pre className="m-0 whitespace-pre-wrap break-all font-mono text-xs text-foreground">
                            {safeStringify(detailResponse)}
                          </pre>
                        </div>
                      </div>
                    )}
                    {!detailRequest && !detailResponse && !event.error && (
                      <div className="text-sm italic text-muted-foreground">
                        No payload captured for this event.
                      </div>
                    )}
                  </div>
                </details>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
