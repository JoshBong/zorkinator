import * as mock from "./mock";
import type {
  EvalSummary,
  Life,
  Move,
  Reflection,
  Rule,
  Run,
  RunMap,
  StreamEvent,
} from "./types";

/**
 * Flip USE_MOCK to false (or set VITE_USE_MOCK="false") once the FastAPI
 * backend is reachable at VITE_API_BASE_URL. Nothing else needs to change.
 */
export const USE_MOCK =
  (import.meta.env["VITE_USE_MOCK"] ?? "true").toString().toLowerCase() !== "false";

export const API_BASE_URL = (import.meta.env["VITE_API_BASE_URL"] ?? "").toString();

const MOCK_LATENCY = 220;

function delay<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), MOCK_LATENCY));
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`Request failed [${res.status}] ${path}: ${await res.text()}`);
  return (await res.json()) as T;
}

export const api = {
  listRuns(): Promise<Run[]> {
    return USE_MOCK ? delay(mock.getRuns()) : get<Run[]>("/api/runs");
  },
  listLives(runId: string): Promise<Life[]> {
    return USE_MOCK ? delay(mock.getLives(runId)) : get<Life[]>(`/api/runs/${runId}/lives`);
  },
  listMoves(runId: string, life: number): Promise<Move[]> {
    return USE_MOCK
      ? delay(mock.getMoves(runId, life))
      : get<Move[]>(`/api/runs/${runId}/lives/${life}/moves`);
  },
  getReflection(runId: string, life: number): Promise<Reflection> {
    return USE_MOCK
      ? delay(mock.getReflection(runId, life))
      : get<Reflection>(`/api/runs/${runId}/lives/${life}/reflection`);
  },
  listRules(): Promise<Rule[]> {
    return USE_MOCK ? delay(mock.getRules()) : get<Rule[]>("/api/rules");
  },
  getMap(runId: string): Promise<RunMap> {
    return USE_MOCK ? delay(mock.getMap(runId)) : get<RunMap>(`/api/runs/${runId}/map`);
  },
  getEvalSummary(): Promise<EvalSummary> {
    return USE_MOCK ? delay(mock.getEvalSummary()) : get<EvalSummary>("/api/eval/summary");
  },
};

/**
 * Subscribes to a live run. Uses SSE, falls back to 2s polling on failure.
 * Returns an unsubscribe function.
 */
export function subscribeToRun(
  runId: string,
  onEvent: (event: StreamEvent) => void,
  onStatus?: (status: "sse" | "polling" | "closed") => void,
): () => void {
  if (USE_MOCK || !API_BASE_URL) {
    onStatus?.("closed");
    return () => {};
  }

  let closed = false;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let source: EventSource | null = null;

  const startPolling = () => {
    if (closed || pollTimer) return;
    onStatus?.("polling");
    pollTimer = setInterval(async () => {
      try {
        const events = await get<StreamEvent[]>(`/api/stream/${runId}/poll`);
        events.forEach(onEvent);
      } catch (err) {
        console.error("poll failed", err);
      }
    }, 2000);
  };

  try {
    source = new EventSource(`${API_BASE_URL}/api/stream/${runId}`);
    onStatus?.("sse");
    (["move", "death", "rule_created", "guardrail_block"] as const).forEach((type) => {
      source?.addEventListener(type, (e) => {
        try {
          onEvent({ type, data: JSON.parse((e as MessageEvent).data) } as StreamEvent);
        } catch (err) {
          console.error("bad SSE payload", err);
        }
      });
    });
    source.onerror = () => {
      source?.close();
      source = null;
      startPolling();
    };
  } catch {
    startPolling();
  }

  return () => {
    closed = true;
    source?.close();
    if (pollTimer) clearInterval(pollTimer);
    onStatus?.("closed");
  };
}
