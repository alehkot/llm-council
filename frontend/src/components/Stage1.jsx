import { useState } from 'react';
import Markdown from './Markdown';
import { Button } from './ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Badge } from './ui/badge';
import { cn } from '../lib/utils';

function formatModelName(model) {
  if (!model) return 'unknown';
  return model.split('/')[1] || model;
}

export default function Stage1({ responses, onStartSideChat }) {
  const [activeTab, setActiveTab] = useState('0');

  if (!responses || responses.length === 0) {
    return null;
  }

  const activeIndex = Number(activeTab);
  const activeResponse = responses[Number.isNaN(activeIndex) ? 0 : activeIndex] || responses[0];

  if (!activeResponse) {
    return null;
  }

  return (
    <Card className="my-6 border-border/60 bg-background/70">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Stage 1: Individual Responses</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="flex h-auto flex-wrap gap-2">
            {responses.map((resp, index) => (
              <TabsTrigger
                key={resp.model || index}
                value={String(index)}
                className={cn(resp.failed && 'opacity-70')}
              >
                <span className="flex items-center gap-1">
                  {resp.model.split('/')[1] || resp.model}
                  {resp.failed && (
                    <span className="text-destructive" role="img" aria-label="Failed">
                      <svg aria-hidden="true" className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="12" cy="12" r="10" />
                        <line x1="12" y1="8" x2="12" y2="12" />
                        <line x1="12" y1="16" x2="12.01" y2="16" />
                      </svg>
                    </span>
                  )}
                </span>
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value={activeTab}>
            <div className="rounded-lg border border-border/60 bg-card p-4">
              <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="font-mono">{activeResponse.model}</span>
                {activeResponse.failed && (
                  <Badge variant="warning" className="text-[10px]">
                    Failed
                  </Badge>
                )}
                {onStartSideChat && !activeResponse.failed && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="ml-auto"
                    onClick={() => onStartSideChat({
                      sourceType: 'stage1',
                      sourceModel: activeResponse.model,
                      sourceTitle: `Stage 1 · ${formatModelName(activeResponse.model)}`,
                      sourcePreview: activeResponse.response,
                    })}
                  >
                    Side Chat
                  </Button>
                )}
              </div>
              <Markdown className={cn(activeResponse.failed && 'text-muted-foreground italic')}>
                {activeResponse.response}
              </Markdown>
            </div>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
