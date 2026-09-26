import { AlertTriangle, Loader2, Inbox } from "lucide-react";

export function LoadingState({ label = "Loading telemetry" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 p-8 text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" />
      <span className="font-mono text-xs tracking-widest uppercase">{label}…</span>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Unknown error";
  return (
    <div className="flex flex-col items-center gap-3 p-8 text-center">
      <AlertTriangle className="h-5 w-5 text-destructive" />
      <p className="font-mono text-xs tracking-widest text-destructive uppercase">Signal lost</p>
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="rounded-md border border-border px-3 py-1.5 text-xs text-foreground hover:bg-secondary"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center gap-2 p-8 text-center text-muted-foreground">
      <Inbox className="h-5 w-5" />
      <p className="font-mono text-xs tracking-widest uppercase">{label}</p>
    </div>
  );
}
