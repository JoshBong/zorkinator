import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useMemo, useState } from "react";
import { Brain, ShieldAlert, Trophy } from "lucide-react";

import { api } from "@/api/client";
import { MOVES_PER_GAME } from "@/api/mock";
import { Terminal } from "@/components/Terminal";
import { MindPanel } from "@/components/MindPanel";
import { MapGraph } from "@/components/MapGraph";
import { ReplayControls } from "@/components/ReplayControls";
import { ErrorState, LoadingState } from "@/components/states";
import { useReplay } from "@/lib/use-replay";
import type { Move } from "@/api/types";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Baseline vs Harness — GRUE LAB" },
      {
        name: "description",
        content:
          "Same model, same move budget: watch a baseline agent and our self-evolving harness play Zork I head to head across 10 games.",
      },
      { property: "og:title", content: "Baseline vs Harness — GRUE LAB" },
      {
        property: "og:description",
        content:
          "Head-to-head Zork: baseline loops, the harness writes rules and memory and scores higher.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: HeadToHead,
});

const BASE_RUN = "baseline-1";
const HARN_RUN = "harness-1";

function streak(moves: Move[], i: number) {
  const cur = moves[i];
  if (!cur) return 0;
  let n = 1;
  for (let j = i - 1; j >= 0 && moves[j]?.command === cur.command; j--) n++;
  return n;
}

function HeadToHead() {
  const [game, setGame] = useState(1);

  const baseLives = useQuery({
    queryKey: ["lives", BASE_RUN],
    queryFn: () => api.listLives(BASE_RUN),
  });
  const harnLives = useQuery({
    queryKey: ["lives", HARN_RUN],
    queryFn: () => api.listLives(HARN_RUN),
  });
  const baseQ = useQuery({
    queryKey: ["moves", BASE_RUN, game],
    queryFn: () => api.listMoves(BASE_RUN, game),
  });
  const harnQ = useQuery({
    queryKey: ["moves", HARN_RUN, game],
    queryFn: () => api.listMoves(HARN_RUN, game),
  });
  const mapQ = useQuery({ queryKey: ["map", HARN_RUN], queryFn: () => api.getMap(HARN_RUN) });
  const rulesQ = useQuery({ queryKey: ["rules"], queryFn: () => api.listRules() });

  const base = useMemo(() => (baseQ.data ?? []).slice(0, MOVES_PER_GAME), [baseQ.data]);
  const harn = useMemo(() => (harnQ.data ?? []).slice(0, MOVES_PER_GAME), [harnQ.data]);
  const length = Math.max(base.length, harn.length);
  const replay = useReplay(length);
  const bIdx = Math.min(replay.index, Math.max(0, base.length - 1));
  const hIdx = Math.min(replay.index, Math.max(0, harn.length - 1));
  const bScore = base[bIdx]?.score ?? 0;
  const hScore = harn[hIdx]?.score ?? 0;
  const bStreak = streak(base, bIdx);
  const hMove = harn[hIdx];

  const visited = useMemo(() => new Set(harn.slice(0, hIdx + 1).map((m) => m.room)), [harn, hIdx]);

  // Rules/memory the harness has built up by this game
  const known = useMemo(() => {
    const learned = new Set(
      (harnLives.data ?? []).filter((l) => l.life < game).flatMap((l) => l.rules_learned),
    );
    const latest = new Map<string, NonNullable<typeof rulesQ.data>[number]>();
    for (const r of rulesQ.data ?? []) {
      if (!learned.has(r.rule_id) || r.status === "rolled_back") continue;
      const prev = latest.get(r.rule_id);
      if (!prev || r.version > prev.version) latest.set(r.rule_id, r);
    }
    return [...latest.values()];
  }, [harnLives.data, rulesQ.data, game]);

  const totals = useMemo(() => {
    const sum = (ls?: { life: number; score: number }[]) =>
      (ls ?? []).filter((l) => l.life <= game).reduce((a, l) => a + l.score, 0);
    return { base: sum(baseLives.data), harn: sum(harnLives.data) };
  }, [baseLives.data, harnLives.data, game]);

  if (baseQ.isError || harnQ.isError)
    return (
      <ErrorState
        error={baseQ.error ?? harnQ.error}
        onRetry={() => {
          baseQ.refetch();
          harnQ.refetch();
        }}
      />
    );

  const lead = hScore - bScore;

  return (
    <div className="space-y-4">
      {/* Game selector */}
      <div className="presenter-hide flex flex-wrap items-center gap-2">
        <span className="panel-title">Game · {MOVES_PER_GAME} moves each</span>
        <div className="flex flex-wrap gap-1">
          {Array.from({ length: 10 }, (_, i) => i + 1).map((g) => (
            <button
              key={g}
              onClick={() => setGame(g)}
              className={`rounded-md px-2.5 py-1 font-mono text-xs ${
                g === game
                  ? "bg-harness/20 text-harness"
                  : "text-muted-foreground hover:bg-secondary"
              }`}
            >
              G{g}
            </button>
          ))}
        </div>
      </div>

      {/* Scoreboard */}
      <div className="panel grid items-center gap-4 p-4 md:grid-cols-[1fr_auto_1fr]">
        <ScoreBlock
          label="Baseline"
          score={bScore}
          total={totals.base}
          tone="baseline"
          note={bStreak >= 3 ? `Repeated action x${bStreak}` : undefined}
        />
        <div className="text-center font-mono">
          <p className="panel-title">
            Game {game} · move {replay.index + 1}/{length || MOVES_PER_GAME}
          </p>
          <p className={`mt-1 text-sm ${lead >= 0 ? "text-harness" : "text-baseline"}`}>
            {lead >= 0 ? "Harness leads by" : "Baseline leads by"} {Math.abs(lead)}
          </p>
        </div>
        <ScoreBlock
          label="Harness"
          score={hScore}
          total={totals.harn}
          tone="harness"
          align="right"
          note={hMove?.guardrail?.blocked ? "Guardrail blocked a fatal move" : undefined}
        />
      </div>

      {baseQ.isLoading || harnQ.isLoading ? (
        <LoadingState label="Syncing both agents" />
      ) : (
        <>
          {/* The two games plus what the harness is doing about it — one view, no scrolling
              to find out why it's winning. */}
          <div className="grid gap-4 xl:grid-cols-[1fr_1fr_360px]">
            <div className="rounded-xl border border-baseline/40 p-1">
              <Terminal moves={base} index={bIdx} life={game} className="h-[420px]" />
            </div>
            <div className="rounded-xl border border-harness/40 p-1">
              <Terminal moves={harn} index={hIdx} life={game} className="h-[420px]" />
            </div>
            <div className="flex flex-col gap-4 xl:h-[420px]">
              <MindPanel move={hMove} className="h-[250px] xl:h-auto xl:min-h-0 xl:flex-[3]" />
              <div className="panel flex h-[150px] flex-col p-3 xl:h-auto xl:min-h-0 xl:flex-[2]">
                <p className="panel-title flex items-center gap-2">
                  <Brain className="h-4 w-4 text-lesson" /> Harness rules & memory · {known.length}
                </p>
                <div className="mt-2 flex-1 space-y-1.5 overflow-y-auto pr-1">
                  {rulesQ.isLoading ? (
                    <LoadingState label="Loading rules" />
                  ) : known.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      No lessons yet — first game starts from scratch.
                    </p>
                  ) : (
                    known.map((r) => (
                      <motion.div
                        key={r.rule_id}
                        initial={{ opacity: 0, x: 8 }}
                        animate={{ opacity: 1, x: 0 }}
                        className="rounded-md border border-lesson/30 bg-lesson/5 p-2"
                      >
                        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-lesson">
                          {r.type === "guardrail" ? (
                            <ShieldAlert className="h-3 w-3 text-guard" />
                          ) : null}
                          {r.rule_id} v{r.version} · {r.type}
                        </div>
                        <p className="mt-1 text-xs">{r.text}</p>
                      </motion.div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>

          <ReplayControls
            moves={harn}
            index={replay.index}
            setIndex={replay.setIndex}
            playing={replay.playing}
            setPlaying={replay.setPlaying}
            speed={replay.speed}
            setSpeed={replay.setSpeed}
            restart={replay.restart}
            live={false}
          />

          {/* The harness's world model — persisted in Atlas (world_facts), rebuilt every game. */}
          {mapQ.data ? (
            <div>
              <MapGraph
                map={mapQ.data}
                visited={visited}
                currentRoom={hMove?.room}
                className="h-[420px]"
              />
              <p className="presenter-hide mt-1.5 px-1 font-mono text-[10px] tracking-widest text-muted-foreground uppercase">
                Persisted in Atlas · world_facts, upserted on every move
              </p>
            </div>
          ) : (
            <div className="panel h-[420px]">
              {mapQ.isError ? (
                <ErrorState error={mapQ.error} onRetry={() => mapQ.refetch()} />
              ) : (
                <LoadingState label="Drawing map" />
              )}
            </div>
          )}

          {/* Per-game score strip */}
          <div className="panel p-4">
            <p className="panel-title flex items-center gap-2">
              <Trophy className="h-4 w-4" /> Final score per game
            </p>
            <div className="mt-3 grid grid-cols-10 gap-1">
              {Array.from({ length: 10 }, (_, i) => {
                const b = baseLives.data?.[i]?.score ?? 0;
                const h = harnLives.data?.[i]?.score ?? 0;
                return (
                  <button
                    key={i}
                    onClick={() => setGame(i + 1)}
                    className={`rounded-md p-1 text-center font-mono text-xs ${i + 1 === game ? "bg-secondary" : "hover:bg-secondary/50"}`}
                  >
                    <div className="text-muted-foreground">G{i + 1}</div>
                    <div className="text-baseline">{b}</div>
                    <div className="text-harness">{h}</div>
                  </button>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function ScoreBlock({
  label,
  score,
  total,
  tone,
  note,
  align = "left",
}: {
  label: string;
  score: number;
  total: number;
  tone: "baseline" | "harness";
  note?: string | undefined;
  align?: "left" | "right";
}) {
  const color = tone === "harness" ? "text-harness" : "text-baseline";
  return (
    <div className={align === "right" ? "md:text-right" : ""}>
      <p className="panel-title">{label} agent</p>
      <motion.p
        key={score}
        initial={{ scale: 1.2 }}
        animate={{ scale: 1 }}
        className={`font-mono text-5xl ${color}`}
      >
        {score}
      </motion.p>
      <p className="font-mono text-xs text-muted-foreground">
        Total so far: <span className={color}>{total}</span>
      </p>
      {note && <p className={`mt-1 font-mono text-xs ${color}`}>⚠ {note}</p>}
    </div>
  );
}
