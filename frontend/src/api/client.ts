import * as mock from "./mock";
import type {
  EvalSummary,
  Life,
  Memory,
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

/** VITE_DATA_MODE="static": serve the real exported games in public/demo-data (no backend). */
export const USE_STATIC =
  (import.meta.env["VITE_DATA_MODE"] ?? "").toString().toLowerCase() === "static";

async function getStatic<T>(path: string, fallback?: T): Promise<T> {
  const res = await fetch(`/demo-data/${path}`, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    if (fallback !== undefined) return fallback;
    throw new Error(`Static data missing [${res.status}] ${path}`);
  }
  return (await res.json()) as T;
}

const EMPTY_REFLECTION: Reflection = {
  cause: "",
  effect: "No reflection logged for this game.",
  lesson_text: "",
  rule_id: "",
};

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
    if (USE_STATIC) return getStatic<Run[]>("runs.json");
    return USE_MOCK ? delay(mock.getRuns()) : get<Run[]>("/api/runs");
  },
  listLives(runId: string): Promise<Life[]> {
    if (USE_STATIC) return getStatic<Life[]>(`${runId}/lives.json`);
    return USE_MOCK ? delay(mock.getLives(runId)) : get<Life[]>(`/api/runs/${runId}/lives`);
  },
  listMoves(runId: string, life: number): Promise<Move[]> {
    if (USE_STATIC) return getStatic<Move[]>(`${runId}/lives/${life}.moves.json`);
    return USE_MOCK
      ? delay(mock.getMoves(runId, life))
      : get<Move[]>(`/api/runs/${runId}/lives/${life}/moves`);
  },
  listMemories(runId: string): Promise<Memory[]> {
    if (USE_STATIC) return getStatic<Memory[]>(`${runId}/memories.json`, []);
    return USE_MOCK ? Promise.resolve([]) : get<Memory[]>(`/api/runs/${runId}/memories`);
  },
  getReflection(runId: string, life: number): Promise<Reflection> {
    if (USE_STATIC)
      return getStatic<Reflection>(`${runId}/lives/${life}.reflection.json`, EMPTY_REFLECTION);
    return USE_MOCK
      ? delay(mock.getReflection(runId, life))
      : get<Reflection>(`/api/runs/${runId}/lives/${life}/reflection`);
  },
  listRules(): Promise<Rule[]> {
    if (USE_STATIC) return getStatic<Rule[]>("rules.json");
    return USE_MOCK ? delay(mock.getRules()) : get<Rule[]>("/api/rules");
  },
  getMap(runId: string): Promise<RunMap> {
    if (USE_STATIC) return getStatic<RunMap>(`${runId}/map.json`);
    return USE_MOCK ? delay(mock.getMap(runId)) : get<RunMap>(`/api/runs/${runId}/map`);
  },
  getEvalSummary(): Promise<EvalSummary> {
    if (USE_STATIC) return getStatic<EvalSummary>("eval_summary.json");
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
  if (USE_MOCK || USE_STATIC || !API_BASE_URL) {
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
