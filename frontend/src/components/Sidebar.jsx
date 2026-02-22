import { Button } from './ui/button';
import { Card, CardContent, CardHeader } from './ui/card';
import { ScrollArea } from './ui/scroll-area';
import { Badge } from './ui/badge';
import { cn } from '../lib/utils';
import { useShallow } from 'zustand/react/shallow';
import { useAppStore } from '../store/useAppStore';

export default function Sidebar({
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
  onClearConversations,
  onClose,
  className,
}) {
  const {
    conversations,
    currentConversationId,
    backendConsultations,
    streamingConsultations,
    messageQueue,
  } = useAppStore(useShallow((state) => ({
    conversations: state.conversations,
    currentConversationId: state.currentConversationId,
    backendConsultations: state.backendConsultations,
    streamingConsultations: state.streamingConsultations,
    messageQueue: state.messageQueue,
  })));
  const activeConsultations = { ...backendConsultations, ...streamingConsultations };
  const messageQueues = currentConversationId ? { [currentConversationId]: messageQueue } : {};
  const hasConversations = conversations.length > 0;

  const handleSelect = (id) => {
    onSelectConversation(id);
    if (onClose) {
      onClose();
    }
  };

  const handleNewConversation = () => {
    onNewConversation();
    if (onClose) {
      onClose();
    }
  };

  return (
    <Card
      as="nav"
      aria-label="Consultations"
      className={cn('sidebar glass flex h-full w-full flex-col', className)}
    >
      <CardHeader className="sidebar-header px-4 pb-3">
        <div className="sidebar-brand flex items-start justify-between gap-3">
          <hgroup>
            <h1 className="sidebar-title text-xl font-semibold leading-none tracking-tight">
              LLM Council
            </h1>
            <p className="sidebar-subtitle mt-1 text-sm text-muted-foreground">
              Consultations
              <Badge variant="subtle" className="sidebar-count ml-2">
                {conversations.length}
              </Badge>
            </p>
          </hgroup>
          {onClose && (
              <Button
                variant="ghost"
                size="icon"
                onClick={onClose}
                aria-label="Close sidebar"
                className="sidebar-close-btn"
              >
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
          )}
        </div>

        <div className="sidebar-actions mt-4 grid gap-2">
          <Button
            onClick={handleNewConversation}
            title="Start a new consultation"
            className="sidebar-new-btn"
          >
            New Consultation
          </Button>
          <Button
            variant="outline"
            onClick={onClearConversations}
            disabled={!hasConversations}
            className="sidebar-clear-btn border-destructive/40 text-destructive hover:bg-destructive/10"
            title="Delete all consultations"
          >
            Delete All
          </Button>
        </div>
      </CardHeader>

      <CardContent className="sidebar-content flex-1 px-4 pb-4 pt-0">
        <ScrollArea className="h-full">
          {conversations.length === 0 ? (
            <p className="conversation-list-empty flex h-10 items-center justify-center rounded-md border border-dashed border-border bg-muted/50 text-sm text-muted-foreground">
              No consultations yet
            </p>
          ) : (
            <ul className="conversation-list flex flex-col gap-2">
              {conversations.map((conv) => {
                const isActive = conv.id === currentConversationId;
                return (
                  <li
                    key={conv.id}
                    className={cn(
                      'conversation-item group flex h-10 items-center gap-2 rounded-md border px-3 transition',
                      isActive
                        ? 'conversation-item--active border-primary/50 bg-primary/10'
                        : 'border-border/60 bg-card/50 hover:border-border hover:bg-muted/70'
                    )}
                    data-conversation-id={conv.id}
                    data-active={isActive || undefined}
                  >
                    <button
                      type="button"
                      onClick={() => handleSelect(conv.id)}
                      className="conversation-select-btn flex min-w-0 flex-1 items-center text-left"
                      aria-current={isActive ? 'true' : undefined}
                      aria-label={`Open consultation: ${conv.title || 'New Consultation'}`}
                    >
                      <span className="conversation-title block truncate text-sm font-semibold text-foreground">
                        {conv.title || 'New Consultation'}
                      </span>
                      {activeConsultations?.[conv.id] && (
                        <Badge variant="outline" className="ml-2 shrink-0 animate-pulse motion-reduce:animate-none text-xs">
                          {activeConsultations[conv.id]}
                        </Badge>
                      )}
                      {messageQueues?.[conv.id]?.length > 0 && (
                        <Badge variant="secondary" className="ml-1 shrink-0 text-xs">
                          +{messageQueues[conv.id].length}
                        </Badge>
                      )}
                    </button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="conversation-delete-btn shrink-0 text-destructive/70 hover:bg-destructive/10 hover:text-destructive"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteConversation(conv.id);
                      }}
                      title="Delete consultation"
                      aria-label={`Delete consultation: ${conv.title || 'New Consultation'}`}
                    >
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
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                        <path d="M10 11v6" />
                        <path d="M14 11v6" />
                        <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
                      </svg>
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
