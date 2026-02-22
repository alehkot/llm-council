import { apiKeyStorage } from '../utils/apiKeyStorage';
import { Button } from './ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

export default function ApiKeyStatus({ onClear }) {
  const expiry = apiKeyStorage.getExpiry();
  const hasKey = !!apiKeyStorage.get();

  const formatExpiry = (date) => {
    if (!date) return 'Unknown';
    return date.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  const handleClear = () => {
    onClear();
  };

  if (!hasKey) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative"
          title="API Key Status"
          aria-label="API key status"
        >
          <svg
            aria-hidden="true"
            className="h-4 w-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"
            />
          </svg>
          <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-emerald-500" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="flex items-center gap-2 text-emerald-600">
          <span className="h-2 w-2 rounded-full bg-emerald-500" />
          API Key Active
        </DropdownMenuLabel>
        {expiry && (
          <p className="px-2 pb-2 text-xs text-muted-foreground">
            Expires: {formatExpiry(expiry)}
          </p>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={handleClear} className="text-destructive focus:text-destructive">
          Clear API Key
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
