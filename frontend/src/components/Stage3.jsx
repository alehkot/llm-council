import { useState, useCallback, useEffect, useMemo } from 'react';
import Markdown from './Markdown';
import ExportMarkdownButton from './ExportMarkdownButton';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from './ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { api } from '../api';

const SYNTHESIS_MODES = [
  { value: 'consensus', label: 'Consensus', description: 'Compact all; common ground' },
  { value: 'best_of_breed', label: 'Best of Breed', description: 'Top answers plus excerpts' },
  { value: 'comprehensive', label: 'Comprehensive', description: 'Broad context from all' },
  { value: 'weighted', label: 'Weighted', description: 'Rank-prioritized context' },
];

/**
 * Format a timestamp for display in tabs.
 * Shows time in HH:MM format.
 */
function formatTimestamp(timestamp) {
  if (!timestamp) return '';
  try {
    const date = new Date(timestamp);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

/**
 * Get display label for a version tab.
 */
function getVersionTabLabel(version, isCurrentVersion) {
  const modeInfo = SYNTHESIS_MODES.find(m => m.value === version.mode) || { label: version.mode };
  const time = formatTimestamp(version.timestamp);
  const label = modeInfo.label || version.mode;
  return `${label}${time ? ` (${time})` : ''}${isCurrentVersion ? ' ★' : ''}`;
}

function getCurrentVersionIndex(stage3Data) {
  if (!stage3Data?.versions?.length) return 0;
  const currentVersion = stage3Data.current;
  const idx = stage3Data.versions.findIndex(
    v => v.timestamp === currentVersion?.timestamp && v.mode === currentVersion?.mode
  );
  return idx >= 0 ? idx : 0;
}

function getStage3SourceKey(version, versionIndex) {
  if (!version) return null;
  const mode = version.mode || 'best_of_breed';
  const timestamp = version.timestamp || versionIndex;
  return `stage3::${mode}::${timestamp}`;
}

/**
 * Normalize stage3 data to handle both old and new formats.
 * Old format: { model: "...", response: "..." }
 * New format: { current: {...}, versions: [...] }
 */
function normalizeStage3(stage3Data) {
  if (!stage3Data) return null;

  // Already in new format
  if (stage3Data.current && stage3Data.versions) {
    return stage3Data;
  }

  // Migrate old format
  const version = {
    model: stage3Data.model,
    response: stage3Data.response,
    mode: stage3Data.mode || 'best_of_breed',
    timestamp: new Date().toISOString(),
  };

  return {
    current: version,
    versions: [version],
  };
}

// Cached conversation navigation reuses this component. Keep each message's UI
// state separate, and let an async handler update only its originating message.
function useMessageState(messageKey, initialValue) {
  const [values, setValues] = useState({});
  const value = Object.hasOwn(values, messageKey) ? values[messageKey] : initialValue;
  const setValue = useCallback((nextValue) => {
    setValues(previous => {
      const current = Object.hasOwn(previous, messageKey) ? previous[messageKey] : initialValue;
      const next = typeof nextValue === 'function' ? nextValue(current) : nextValue;
      if (Object.is(current, next)) return previous;
      return { ...previous, [messageKey]: next };
    });
  }, [messageKey, initialValue]);
  return [value, setValue];
}

export default function Stage3({
  finalResponse, // Can be old format or new format
  conversationId,
  messageIndex,
  onVersionAdded,
  onCurrentChanged,
  onStartSideChat,
}) {
  const messageKey = `${conversationId}:${messageIndex}`;
  const stage3Data = useMemo(() => normalizeStage3(finalResponse), [finalResponse]);
  const currentVersionIndex = useMemo(() => getCurrentVersionIndex(stage3Data), [stage3Data]);
  const [copied, setCopied] = useMessageState(messageKey, false);
  const [selectedMode, setSelectedMode] = useMessageState(messageKey, 'best_of_breed');
  const [isResynthesizing, setIsResynthesizing] = useMessageState(messageKey, false);
  const [isGeneratingAll, setIsGeneratingAll] = useMessageState(messageKey, false);
  const [resynthError, setResynthError] = useMessageState(messageKey, null);
  const [activeVersionIndex, setActiveVersionIndex] = useMessageState(messageKey, currentVersionIndex);

  useEffect(() => {
    setActiveVersionIndex(currentVersionIndex);
  }, [messageKey, currentVersionIndex, setActiveVersionIndex]);

  // Get the currently displayed version
  const resolvedActiveVersionIndex = stage3Data?.versions?.[activeVersionIndex]
    ? activeVersionIndex
    : currentVersionIndex;
  const activeVersion = stage3Data?.versions?.[resolvedActiveVersionIndex] || stage3Data?.current;
  const currentVersion = stage3Data?.current;

  // Check if active version is the current one
  const isActiveVersionCurrent = activeVersion && currentVersion &&
    activeVersion.timestamp === currentVersion.timestamp &&
    activeVersion.mode === currentVersion.mode;

  // Get display info for the current synthesis mode
  const currentModeInfo = SYNTHESIS_MODES.find(m => m.value === activeVersion?.mode) || SYNTHESIS_MODES[1];

  // Calculate which modes are missing (for "Generate All Missing" button)
  const existingModes = useMemo(() => {
    if (!stage3Data?.versions) return new Set();
    return new Set(stage3Data.versions.map(v => v.mode));
  }, [stage3Data?.versions]);

  const missingModes = useMemo(() => {
    return SYNTHESIS_MODES.filter(m => !existingModes.has(m.value));
  }, [existingModes]);

  const handleCopyMarkdown = useCallback(async () => {
    if (!activeVersion?.response) return;

    try {
      await navigator.clipboard.writeText(activeVersion.response);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy to clipboard:', err);
    }
  }, [activeVersion?.response, setCopied]);

  const handleResynthesize = useCallback(async () => {
    if (!conversationId || messageIndex === undefined) return;

    setIsResynthesizing(true);
    setResynthError(null);

    try {
      const result = await api.resynthesize(conversationId, messageIndex, selectedMode);
      if (onVersionAdded && result.version) {
        onVersionAdded(result.version, { setAsCurrent: true });
        // Switch to the new version tab (assuming it's appended at the end)
        const newIndex = (stage3Data?.versions?.length || 0);
        setActiveVersionIndex(newIndex);
      }
    } catch (err) {
      console.error('Resynthesis failed:', err);
      setResynthError(err.message);
    } finally {
      setIsResynthesizing(false);
    }
  }, [conversationId, messageIndex, selectedMode, onVersionAdded, stage3Data?.versions?.length, setIsResynthesizing, setResynthError, setActiveVersionIndex]);

  const handleGenerateAll = useCallback(async () => {
    if (!conversationId || messageIndex === undefined || missingModes.length === 0) return;

    setIsGeneratingAll(true);
    setResynthError(null);

    try {
      const result = await api.resynthesizeAll(conversationId, messageIndex);
      if (onVersionAdded && result.versions) {
        // Add all new versions
        result.versions.forEach((version) => {
          onVersionAdded(version, { setAsCurrent: false });
        });
      }
      if (result.errors && result.errors.length > 0) {
        setResynthError(`Some modes failed: ${result.errors.map(e => e.mode).join(', ')}`);
      }
    } catch (err) {
      console.error('Generate all failed:', err);
      setResynthError(err.message);
    } finally {
      setIsGeneratingAll(false);
    }
  }, [conversationId, messageIndex, missingModes, onVersionAdded, setIsGeneratingAll, setResynthError]);

  const handleSetAsCurrent = useCallback(async () => {
    if (!conversationId || messageIndex === undefined || isActiveVersionCurrent) return;

    try {
      await api.setCurrentVersion(conversationId, messageIndex, resolvedActiveVersionIndex);
      if (onCurrentChanged) {
        onCurrentChanged(resolvedActiveVersionIndex);
      }
    } catch (err) {
      console.error('Failed to set current version:', err);
      setResynthError(err.message);
    }
  }, [
    conversationId,
    messageIndex,
    isActiveVersionCurrent,
    onCurrentChanged,
    resolvedActiveVersionIndex,
    setResynthError,
  ]);

  if (!stage3Data) {
    return null;
  }

  const modelLabel = activeVersion?.model?.split('/')[1] || activeVersion?.model || 'Unknown';
  const canResynthesize = conversationId && messageIndex !== undefined;
  const hasMultipleVersions = stage3Data.versions.length > 1;

  return (
    <Card className="stage3 my-6 dark:border-emerald-700/30 dark:bg-emerald-950/20">
      <CardHeader className="stage3-header pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <CardTitle className="stage3-title text-base">Stage 3: Final Council Answer</CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              <Badge
                variant="outline"
                className="stage3-badge stage3-badge-chairman w-fit text-[10px] dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-700/60"
              >
                Chairman: {modelLabel}
              </Badge>
              <Badge
                variant="outline"
                className="stage3-badge stage3-badge-mode w-fit text-[10px] dark:bg-emerald-900/30 dark:text-emerald-400 dark:border-emerald-700/50"
              >
                {currentModeInfo.label}: {currentModeInfo.description}
              </Badge>
              {isActiveVersionCurrent && (
                <Badge
                  variant="outline"
                  className="stage3-badge stage3-badge-current w-fit text-[10px] dark:bg-amber-900/30 dark:text-amber-400 dark:border-amber-700/50"
                >
                  ★ Current
                </Badge>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {onStartSideChat && activeVersion?.response && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => onStartSideChat({
                  sourceType: 'stage3',
                  sourceModel: activeVersion.model,
                  versionIndex: resolvedActiveVersionIndex,
                  sourceMode: activeVersion.mode || 'best_of_breed',
                  sourceTimestamp: activeVersion.timestamp || resolvedActiveVersionIndex,
                  sourceKey: getStage3SourceKey(activeVersion, resolvedActiveVersionIndex),
                  sourceTitle: `Synthesis · ${currentModeInfo.label}`,
                  sourcePreview: activeVersion.response,
                })}
                className="stage3-action gap-1.5"
              >
                Side Chat
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={handleCopyMarkdown}
              className="stage3-action gap-1.5"
            >
              {copied ? (
                <>
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    className="h-3.5 w-3.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                  Copied!
                </>
              ) : (
                <>
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    className="h-3.5 w-3.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                  </svg>
                  Copy
                </>
              )}
            </Button>
            {canResynthesize && (
              <ExportMarkdownButton
                conversationId={conversationId}
                messageIndex={messageIndex}
                className="stage3-action gap-1.5"
                onError={setResynthError}
              />
            )}
            <Dialog>
              <DialogTrigger asChild>
                <Button variant="outline" size="sm" className="stage3-action">
                  Focus View
                </Button>
              </DialogTrigger>
              <DialogContent className="flex max-h-[85vh] w-full max-w-4xl flex-col gap-0 overflow-hidden p-0">
                <DialogHeader className="flex-none border-b border-border/60 bg-background/80 px-6 py-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-1">
                      <DialogTitle>Final Council Answer</DialogTitle>
                      <DialogDescription>Chairman: {modelLabel}</DialogDescription>
                    </div>
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
                  </div>
                </DialogHeader>
                <div className="flex-1 overflow-y-auto bg-gradient-to-b from-background via-background to-muted/40 px-6 py-5">
                  <div className="mx-auto max-w-3xl pr-4">
                    <Markdown className="text-[16px] leading-relaxed">
                      {activeVersion?.response || ''}
                    </Markdown>
                  </div>
                </div>
              </DialogContent>
            </Dialog>
            {canResynthesize && missingModes.length > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleGenerateAll}
                disabled={isGeneratingAll || isResynthesizing}
                className="stage3-action gap-1.5"
              >
                {isGeneratingAll ? (
                  <>
                    <svg
                      aria-hidden="true"
                      viewBox="0 0 24 24"
                      className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                    >
                      <circle cx="12" cy="12" r="10" className="opacity-25" />
                      <path d="M12 2a10 10 0 0 1 10 10" className="opacity-75" />
                    </svg>
                    Generating…
                  </>
                ) : (
                  <>
                    <svg
                      aria-hidden="true"
                      viewBox="0 0 24 24"
                      className="h-3.5 w-3.5"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <rect x="3" y="3" width="7" height="7" />
                      <rect x="14" y="3" width="7" height="7" />
                      <rect x="14" y="14" width="7" height="7" />
                      <rect x="3" y="14" width="7" height="7" />
                    </svg>
                    Generate All
                  </>
                )}
              </Button>
            )}
          </div>
        </div>

        {/* Version tabs */}
        {hasMultipleVersions && (
          <div className="mt-4">
            <Tabs
              value={String(activeVersionIndex)}
              onValueChange={(v) => setActiveVersionIndex(Number(v))}
            >
              <TabsList className="stage3-tabs h-auto flex-wrap gap-1 bg-transparent p-0">
                {stage3Data.versions.map((version, index) => {
                  const isCurrentTab = version.timestamp === currentVersion?.timestamp &&
                    version.mode === currentVersion?.mode;
                  return (
                    <TabsTrigger
                      key={`${version.mode}-${version.timestamp}-${index}`}
                      value={String(index)}
                      className="stage3-tab text-xs dark:data-[state=active]:bg-emerald-900/40"
                    >
                      {getVersionTabLabel(version, isCurrentTab)}
                    </TabsTrigger>
                  );
                })}
              </TabsList>
            </Tabs>
          </div>
        )}

        {/* Resynthesis controls */}
        {canResynthesize && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Select value={selectedMode} onValueChange={setSelectedMode}>
              <SelectTrigger className="stage3-select h-8 w-[160px] text-xs">
                <SelectValue placeholder="Synthesis mode" />
              </SelectTrigger>
              <SelectContent>
                {SYNTHESIS_MODES.map((mode) => (
                  <SelectItem key={mode.value} value={mode.value} className="text-xs">
                    <div className="flex flex-col">
                      <span>{mode.label}</span>
                      <span className="text-muted-foreground text-[10px]">{mode.description}</span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="sm"
              onClick={handleResynthesize}
              disabled={isResynthesizing || isGeneratingAll}
              className="stage3-action gap-1.5"
            >
              {isResynthesizing ? (
                <>
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <circle cx="12" cy="12" r="10" className="opacity-25" />
                    <path d="M12 2a10 10 0 0 1 10 10" className="opacity-75" />
                  </svg>
                  Adding…
                </>
              ) : (
                <>
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    className="h-3.5 w-3.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <line x1="12" y1="5" x2="12" y2="19" />
                    <line x1="5" y1="12" x2="19" y2="12" />
                  </svg>
                  Add Synthesis
                </>
              )}
            </Button>
            {hasMultipleVersions && !isActiveVersionCurrent && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleSetAsCurrent}
                className="stage3-action gap-1.5"
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  className="h-3.5 w-3.5"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                </svg>
                Set as Current
              </Button>
            )}
          </div>
        )}

        {resynthError && (
          <p className="mt-2 text-sm text-destructive">{resynthError}</p>
        )}
      </CardHeader>
      <CardContent className="stage3-body">
        <Markdown className="stage3-markdown text-[15px] leading-relaxed">
          {activeVersion?.response || ''}
        </Markdown>
      </CardContent>
    </Card>
  );
}
