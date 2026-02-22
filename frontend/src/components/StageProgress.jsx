import { useMemo } from 'react';
import { cn } from '../lib/utils';

const STATUS_LABELS = {
  pending: 'Queued',
  running: 'Running',
  complete: 'Done',
  failed: 'Failed',
};

const STATUS_ORDER = ['running', 'pending', 'complete', 'failed'];

const normalizeStatus = (status) => {
  if (!status) return 'pending';
  const value = status.toLowerCase();
  if (value === 'completed') return 'complete';
  if (value === 'error') return 'failed';
  if (STATUS_LABELS[value]) return value;
  return 'pending';
};

export default function StageProgress({ progress }) {
  const normalized = progress || {};
  const entries = useMemo(() => {
    const models = normalized.models || {};
    const order = Array.isArray(normalized.order)
      ? normalized.order
      : Object.keys(models);

    return order
      .filter((model) => model in models)
      .map((model) => {
        const status = normalizeStatus(models[model]?.status);
        return { model, status };
      });
  }, [normalized.models, normalized.order]);

  if (!entries.length) {
    return null;
  }

  const counts = normalized.counts || {};
  const total = Number.isFinite(normalized.total) ? normalized.total : entries.length;
  const completed = Number.isFinite(normalized.completed)
    ? normalized.completed
    : (counts.complete || 0) + (counts.failed || 0);
  const failed = Number.isFinite(normalized.failed) ? normalized.failed : counts.failed || 0;

  return (
    <details className="stage-progress">
      <summary className="stage-progress-summary">
        <span className="stage-progress-summary-label">Stage details</span>
        <span className="stage-progress-summary-count">
          {completed}/{total} complete
        </span>
        {failed > 0 && (
          <span className="stage-progress-summary-failed">{failed} failed</span>
        )}
      </summary>
      <div className="stage-progress-body">
        <div className="stage-progress-grid">
          {entries
            .slice()
            .sort((a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status))
            .map((entry) => (
              <div key={entry.model} className="stage-progress-row">
                <span
                  className={cn('stage-progress-dot', `stage-progress-dot-${entry.status}`)}
                  aria-hidden="true"
                />
                <span className="stage-progress-model" title={entry.model}>{entry.model}</span>
                <span
                  className={cn('stage-progress-status', `stage-progress-status-${entry.status}`)}
                >
                  {STATUS_LABELS[entry.status] || entry.status}
                </span>
              </div>
            ))}
        </div>
      </div>
    </details>
  );
}
