import { useEffect, useState } from 'react';
import { Dialog, DialogClose, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { api } from '../api';

function StatusRow({ label, value, isEnabled }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border/60 bg-background/70 px-3 py-2">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="m-0">
        {typeof isEnabled === 'boolean' ? (
          <Badge variant={isEnabled ? 'success' : 'subtle'}>
            {isEnabled ? 'Enabled' : 'Disabled'}
          </Badge>
        ) : (
          <span className="text-sm font-medium text-foreground">{value}</span>
        )}
      </dd>
    </div>
  );
}

function ModelList({ models }) {
  return (
    <div className="flex flex-wrap gap-2">
      {models.map((model) => (
        <Badge key={model} variant="outline" className="font-mono text-[11px]">
          {model}
        </Badge>
      ))}
    </div>
  );
}

export default function StatusModal({ config, isOpen, onClose }) {
  const [consultationStatus, setConsultationStatus] = useState(null);

  useEffect(() => {
    if (!isOpen) return;

    const fetchStatus = async () => {
      try {
        const status = await api.getGlobalConsultationStatus();
        setConsultationStatus(status);
      } catch (err) {
        console.error('Failed to fetch consultation status:', err);
      }
    };

    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, [isOpen]);

  if (!config) return null;

  const pool = consultationStatus?.pool;
  const consultations = consultationStatus?.consultations;

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader className="flex items-start justify-between gap-4 sm:flex-row">
          <DialogTitle>System Status</DialogTitle>
          <DialogClose asChild>
            <Button variant="ghost" size="icon" aria-label="Close">
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                className="h-4 w-4"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </Button>
          </DialogClose>
        </DialogHeader>

        <div className="space-y-6">
          {pool && (
            <section className="space-y-3">
              <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
                Consultation Pool
              </h3>
              <dl className="space-y-2">
                <StatusRow
                  label="Workers"
                  value={`${pool.available} available / ${pool.size} total`}
                />
                <StatusRow
                  label="Utilization"
                  value={`${Math.round(pool.utilization * 100)}%`}
                />
                {consultations && (
                  <>
                    <StatusRow
                      label="Active Consultations"
                      value={consultations.active_count}
                    />
                    <StatusRow
                      label="Queued Messages"
                      value={consultations.queued_count}
                    />
                  </>
                )}
              </dl>
            </section>
          )}

          <section className="space-y-3">
            <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
              Feature Flags
            </h3>
            <dl className="space-y-2">
              <StatusRow label="Negative Mode" isEnabled={config.negative_mode_enabled} />
              <StatusRow label="Deliberation Mode" isEnabled={config.deliberation_enabled} />
              <StatusRow label="Interactive Mode" isEnabled={config.interactive_mode_enabled} />
              <StatusRow label="Prompt Refinement" isEnabled={config.prompt_refinement_enabled} />
              <StatusRow label="Pairwise Ranking (Schulze)" isEnabled={config.pairwise_ranking_enabled} />
            </dl>
          </section>

          <section className="space-y-3">
            <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
              Limits
            </h3>
            <dl className="space-y-2">
              <StatusRow label="Prompt max length" value={`${config.prompt_max_chars ?? 'n/a'} chars`} />
              <StatusRow label="Negative prompt max length" value={`${config.negative_prompt_max_chars ?? 'n/a'} chars`} />
              <StatusRow label="OpenRouter max tokens" value={`${config.openrouter_max_tokens ?? 'n/a'} tokens`} />
            </dl>
          </section>

          <section className="space-y-3">
            <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
              Council Models
            </h3>
            <Card className="border-border/60 bg-muted/40 p-4">
              {config.council_models && config.council_models.length > 0 ? (
                <ModelList models={config.council_models} />
              ) : (
                <p className="text-sm text-muted-foreground">No models configured</p>
              )}
            </Card>
          </section>

          <section className="space-y-3">
            <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
              Special Models
            </h3>
            <dl className="space-y-2">
              <StatusRow label="Chairman Model" value={config.chairman_model || 'Not set'} />
              <StatusRow label="Title Generation Model" value={config.title_model || 'Not set'} />
            </dl>
          </section>

          <section className="space-y-3">
            <h3 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">
              Settings
            </h3>
            <dl>
              <StatusRow
                label="Stream Timeout"
                value={config.stream_timeout_seconds ? `${config.stream_timeout_seconds}s` : 'Default'}
              />
            </dl>
          </section>
        </div>

        <p className="text-center text-xs text-muted-foreground">
          Configure these settings via environment variables in <code className="rounded bg-muted px-1">.env</code>
        </p>
      </DialogContent>
    </Dialog>
  );
}
