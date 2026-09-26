import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useMemo } from "react";

import { api } from "@/api/client";
import { Terminal } from "@/components/Terminal";
import { ReplayControls } from "@/components/ReplayControls";
import { ErrorState, LoadingState } from "@/components/states";
import { useReplay } from "@/lib/use-replay";
import type { Move } from "@/api/types";

export const Route = createFileRoute("/race")({
  head: () => ({
    meta: [
      { title: "Race Mode — GRUE LAB" },
      {
        name: "description",
        content: "Baseline agent versus the self-evolving harness, replaying the same game in sync.",
      },
      { property: "og:title", content: "Race Mode — GRUE LAB" },
      {
        property: "og:description",
        content: "Side-by-side Zork replay: the baseline loops, the harness learns.",
      },
    ],
  }),
  component: RaceMode,
});

const LIFE = 6;

function repeatStreak(moves: Move[], index: number) {
  const cur = moves[index];
  if (!cur) return 0;
  let n = 1;
  for (let i = index - 1; i >= 0 && moves[i]?.command === cur.command; i--) n++;
  return n;
}

function RaceMode() {
  const baseQ = useQuery({
    queryKey: ["moves", "baseline-1", LIFE],
    queryFn: () => api.listMoves("baseline-1", LIFE),
  });
  const harnQ = useQuery({
    queryKey: ["moves", "harness-1", LIFE],
    queryFn: () => api.listMoves("harness-1", LIFE),
  });

  const base = useMemo(() => baseQ.data ?? [], [baseQ.data]);
  const harn = useMemo(() => harnQ.data ?? [], [harnQ.data]);
  const length = Math.max(base.length, harn.length);
  const replay = useReplay(length);

  const bIdx = Math.min(replay.index, Math.max(0, base.length - 1));
  const hIdx = Math.min(replay.index, Math.max(0, harn.length - 1));
  const bStreak = repeatStreak(base, bIdx);
  const finished = length > 0 && replay.index >= length - 1;

  if (baseQ.isError || harnQ.isError)
    return <ErrorState error={baseQ.error ?? harnQ.error} onRetry={() => baseQ.refetch()} />;
  if (baseQ.isLoading || harnQ.isLoading) return <LoadingState label="Syncing both agents" />;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <ScoreHeader
          label="Baseline agent"
          score={base[bIdx]?.score ?? 0}
          tone="baseline"
          note={bStreak >= 3 ? `Repeated action x${bStreak}` : undefined}
        />
        <ScoreHeader
          label="Harness agent"
          score={harn[hIdx]?.score ?? 0}
          tone="harness"
          note={harn[hIdx]?.guardrail?.blocked ? "Guardrail blocked a fatal move" : undefined}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-xl border border-baseline/40 p-1">
          <Terminal moves={base} index={bIdx} life={LIFE} className="h-[440px]" />
        </div>
        <div className="rounded-xl border border-harness/40 p-1">
          <Terminal moves={harn} index={hIdx} life={LIFE} className="h-[440px]" />
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

      {finished && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="panel grid gap-4 p-6 md:grid-cols-2"
        >
          <FinalCard
            label="Baseline final"
            score={base[base.length - 1]?.score ?? 0}
            tone="baseline"
          />
          <FinalCard
            label="Harness final"
            score={harn[harn.length - 1]?.score ?? 0}
            tone="harness"
          />
        </motion.div>
      )}
    </div>
  );
}

function ScoreHeader({
  label,
  score,
  tone,
  note,
}: {
  label: string;
  score: number;
  tone: "baseline" | "harness";
  note?: string | undefined;
}) {
  const color = tone === "harness" ? "text-harness" : "text-baseline";
  return (
    <div className="panel flex items-center justify-between px-4 py-3">
      <div>
        <p className="panel-title">{label}</p>
        {note && <p className={`mt-1 font-mono text-xs ${color}`}>⚠ {note}</p>}
      </div>
      <motion.span key={score} initial={{ scale: 1.25 }} animate={{ scale: 1 }} className={`font-mono text-3xl ${color}`}>
        {score}
      </motion.span>
    </div>
  );
}

function FinalCard({
  label,
  score,
  tone,
}: {
  label: string;
  score: number;
  tone: "baseline" | "harness";
}) {
  const color = tone === "harness" ? "text-harness" : "text-baseline";
  return (
    <div className="text-center">
      <p className="panel-title">{label}</p>
      <p className={`font-mono text-6xl ${color}`}>{score}</p>
      <p className="mt-1 text-xs text-muted-foreground">points of 350</p>
    </div>
  );
}
