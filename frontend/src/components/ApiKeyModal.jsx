import { useEffect, useRef, useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Alert, AlertDescription } from './ui/alert';

function ApiKeyForm({ isOpen, onSubmit, error }) {
  const inputRef = useRef(null);
  const [apiKey, setApiKey] = useState('');
  const [localError, setLocalError] = useState('');

  useEffect(() => {
    if (!isOpen) return undefined;
    const timer = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(timer);
  }, [isOpen]);

  const handleSubmit = (e) => {
    e.preventDefault();
    const trimmedKey = apiKey.trim();

    if (!trimmedKey) {
      setLocalError('Please enter an API key');
      return;
    }

    onSubmit(trimmedKey);
  };

  const displayError = error || localError;

  return (
    <>
      <DialogHeader className="space-y-3">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10">
            <svg
              aria-hidden="true"
              className="h-5 w-5 text-primary"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
            </svg>
          </div>
          <div>
            <DialogTitle className="flex items-center gap-2">
              Authentication Required
              <Badge variant="outline" className="text-[10px] font-normal">
                Required
              </Badge>
            </DialogTitle>
            <DialogDescription className="mt-1">
              Enter your API key to access LLM Council
            </DialogDescription>
          </div>
        </div>
      </DialogHeader>

      {displayError && (
        <Alert variant="destructive">
          <AlertDescription className="text-destructive/80">{displayError}</AlertDescription>
        </Alert>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="apiKey">
            API Key <span className="text-destructive">*</span>
          </Label>
          <Input
            ref={inputRef}
            id="apiKey"
            name="apiKey"
            type="password"
            value={apiKey}
            onChange={(e) => {
              setApiKey(e.target.value);
              setLocalError('');
            }}
            placeholder={'Enter your API key… e.g., "or-abc123def456"'}
            className="font-mono text-sm"
            autoComplete="off"
            spellCheck="false"
            required
            aria-required="true"
          />
          <p className="text-xs text-muted-foreground">
            Your key will be stored locally for 7 days.
          </p>
        </div>

        <Button type="submit" className="w-full">
          Save API Key
        </Button>
      </form>

      <p className="text-center text-xs text-muted-foreground">
        Contact your administrator if you need an API key.
      </p>
    </>
  );
}

export default function ApiKeyModal({ isOpen, onSubmit, error }) {
  return (
    <Dialog open={isOpen} onOpenChange={() => {}}>
      <DialogContent
        className="sm:max-w-md [&>button]:hidden"
        onEscapeKeyDown={(event) => event.preventDefault()}
        onPointerDownOutside={(event) => event.preventDefault()}
      >
        <ApiKeyForm key={isOpen ? 'open' : 'closed'} isOpen={isOpen} onSubmit={onSubmit} error={error} />
      </DialogContent>
    </Dialog>
  );
}
