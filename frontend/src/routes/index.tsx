import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Brain, Play, ShieldAlert, Trophy } from "lucide-react";

import { api } from "@/api/client";
import { Terminal } from "@/components/Terminal";
import { MindPanel } from "@/components/MindPanel";
import { MapGraph } from "@/components/MapGraph";
import { ReplayControls } from "@/components/ReplayControls";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { useReplay } from "@/lib/use-replay";
import type { Move } from "@/api/types";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "GRUE LAB" },
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

function streak(moves: Move[], i: number) {
  const cur = moves[i];
  if (!cur) return 0;
  let n = 1;
  for (let j = i - 1; j >= 0 && moves[j]?.command === cur.command; j--) n++;
  return n;
}

/** Pause after a game finishes so its final state (including any death) is visible
 * before demo mode cuts away to the next game. */
const DEMO_ADVANCE_DELAY_MS = 1500;

function HeadToHead() {
  const runsQ = useQuery({ queryKey: ["runs"], queryFn: () => api.listRuns() });

  // Real Atlas chains vary in length, have no baseline (paper-mode) run logged yet, and
  // number lives from 0 (mock data numbers them from 1) — so the run to feature and its
  // game list are derived from what's actually there instead of fixed IDs/counts.
  const harnRun = useMemo(() => {
    const harnRuns = (runsQ.data ?? []).filter((r) => r.condition === "harness");
    return [...harnRuns].sort(
      (a, b) => b.lives_count - a.lives_count || b.created_at.localeCompare(a.created_at),
    )[0];
  }, [runsQ.data]);
  const baseRun = useMemo(
    () => (runsQ.data ?? []).find((r) => r.condition === "baseline"),
    [runsQ.data],
  );

  const [game, setGame] = useState(0);
  const [demoMode, setDemoMode] = useState(false);

  const baseLives = useQuery({
    queryKey: ["lives", baseRun?.run_id],
    queryFn: () => api.listLives(baseRun!.run_id),
    enabled: !!baseRun,
  });
  const harnLives = useQuery({
    queryKey: ["lives", harnRun?.run_id],
    queryFn: () => api.listLives(harnRun!.run_id),
    enabled: !!harnRun,
  });
  const baseQ = useQuery({
    queryKey: ["moves", baseRun?.run_id, game],
    queryFn: () => api.listMoves(baseRun!.run_id, game),
    enabled: !!baseRun,
  });
  const harnQ = useQuery({
    queryKey: ["moves", harnRun?.run_id, game],
    queryFn: () => api.listMoves(harnRun!.run_id, game),
    enabled: !!harnRun,
  });
  const mapQ = useQuery({
    queryKey: ["map", harnRun?.run_id],
    queryFn: () => api.getMap(harnRun!.run_id),
    enabled: !!harnRun,
  });
  const rulesQ = useQuery({ queryKey: ["rules"], queryFn: () => api.listRules() });

  // The games available to step through, and a display offset so a 0-based real chain still
  // reads as "Game 1, 2, 3…" while already-1-based data (mock) is shown unchanged.
  const games = useMemo(
    () => [...new Set((harnLives.data ?? []).map((l) => l.life))].sort((a, b) => a - b),
    [harnLives.data],
  );
  const gameOffset = games[0] === 0 ? 1 : 0;

  useEffect(() => {
    if (games.length > 0 && !games.includes(game)) setGame(games[0]!);
  }, [games, game]);

  const base = useMemo(() => baseQ.data ?? [], [baseQ.data]);
  const harn = useMemo(() => harnQ.data ?? [], [harnQ.data]);
  const length = Math.max(base.length, harn.length);
  const replay = useReplay(length);

  // Demo mode: once a game's replay stops because it *finished* (sitting on the last move,
  // not paused mid-game), wait a beat so the final state is visible, then move to the next
  // game in `games`. `advancedForGame` stops the effect from scheduling a second advance for
  // the same game while the first timeout is still pending or the next game hasn't loaded.
  const advancedForGame = useRef<number | null>(null);
  useEffect(() => {
    if (!demoMode || length === 0) return;
    const finished = replay.index === length - 1 && !replay.playing;
    const idx = games.indexOf(game);
    const isLastGame = idx === -1 || idx >= games.length - 1;
    if (!finished || isLastGame) {
      advancedForGame.current = null;
      return;
    }
    if (advancedForGame.current === game) return;
    advancedForGame.current = game;
    const nextGame = games[idx + 1]!;
    const timer = setTimeout(() => {
      setGame(nextGame);
      replay.setIndex(0);
      replay.setPlaying(true);
    }, DEMO_ADVANCE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [
    demoMode,
    replay.index,
    replay.playing,
    replay.setIndex,
    replay.setPlaying,
    length,
    game,
    games,
  ]);

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

  if (runsQ.isError) return <ErrorState error={runsQ.error} onRetry={() => runsQ.refetch()} />;
  if (runsQ.isLoading) return <LoadingState label="Loading runs from Atlas" />;
  if (!harnRun) return <EmptyState label="No harness runs logged in Atlas yet" />;
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
  const moveCap = harn.length || base.length;

  return (
    <div className="space-y-3">
      {/* Game selector */}
      <div className="presenter-hide flex flex-wrap items-center gap-2">
        <span className="panel-title">Game{moveCap ? ` · ${moveCap} moves each` : ""}</span>
        <div className="flex flex-wrap gap-1">
          {games.map((g) => (
            <button
              key={g}
              onClick={() => setGame(g)}
              className={`rounded-md px-2.5 py-1 font-mono text-xs ${
                g === game
                  ? "bg-harness/20 text-harness"
                  : "text-muted-foreground hover:bg-secondary"
              }`}
            >
              G{g + gameOffset}
            </button>
          ))}
        </div>
        <button
          onClick={() => setDemoMode((d) => !d)}
          className={`ml-auto flex items-center gap-1.5 rounded-md px-2.5 py-1 font-mono text-xs ${
            demoMode ? "bg-harness/20 text-harness" : "text-muted-foreground hover:bg-secondary"
          }`}
        >
          <Play className="h-3 w-3" />
          Demo mode {demoMode ? "on" : "off"}
        </button>
      </div>

      {/* Scoreboard */}
      <div className="panel grid items-center gap-4 p-3 md:grid-cols-[1fr_auto_1fr]">
        <ScoreBlock
          label="Baseline"
          score={bScore}
          total={totals.base}
          tone="baseline"
          note={
            !baseRun
              ? "No baseline run logged yet"
              : bStreak >= 3
                ? `Repeated action x${bStreak}`
                : undefined
          }
        />
        <div className="text-center font-mono">
          <p className="panel-title">
            Game {game + gameOffset} · move {replay.index + 1}/{length || 1}
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

      {harnQ.isLoading || (!!baseRun && baseQ.isLoading) ? (
        <LoadingState label="Syncing both agents" />
      ) : (
        <>
          {/* The two games plus what the harness is doing about it — one view, no scrolling
              to find out why it's winning. Mind and Rules each get their own column so
              neither is squeezed into a shared sidebar. */}
          <div className="grid gap-3 xl:grid-cols-[1fr_1fr_290px_290px]">
            <div className="rounded-xl border border-baseline/40 p-1">
              {baseRun ? (
                <Terminal moves={base} index={bIdx} life={game} className="h-[250px]" />
              ) : (
                <div className="panel flex h-[250px] items-center justify-center">
                  <EmptyState label="No baseline run logged yet" />
                </div>
              )}
            </div>
            <div className="rounded-xl border border-harness/40 p-1">
              <Terminal moves={harn} index={hIdx} life={game} className="h-[250px]" />
            </div>
            <MindPanel move={hMove} className="h-[250px]" />
            <div className="panel flex h-[250px] flex-col p-3">
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
                previousRoom={harn[hIdx - 1]?.room}
                currentGame={game}
                className="h-[250px]"
              />
              <p className="presenter-hide mt-1 px-1 font-mono text-[10px] tracking-widest text-muted-foreground uppercase">
                Persisted in Atlas · red = died here
              </p>
            </div>
          ) : (
            <div className="panel h-[250px]">
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
            <div
              className="mt-3 grid gap-1"
              style={{ gridTemplateColumns: `repeat(${games.length || 1}, minmax(0, 1fr))` }}
            >
              {games.map((g) => {
                const b = baseLives.data?.find((l) => l.life === g)?.score ?? 0;
                const h = harnLives.data?.find((l) => l.life === g)?.score ?? 0;
                return (
                  <button
                    key={g}
                    onClick={() => setGame(g)}
                    className={`rounded-md p-1 text-center font-mono text-xs ${g === game ? "bg-secondary" : "hover:bg-secondary/50"}`}
                  >
                    <div className="text-muted-foreground">G{g + gameOffset}</div>
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
