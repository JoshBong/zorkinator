import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useState } from "react";
import { Skull } from "lucide-react";

import { api } from "@/api/client";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export const Route = createFileRoute("/graveyard")({
  head: () => ({
    meta: [
      { title: "GRUE LAB" },
      {
        name: "description",
        content:
          "Every death the agent suffered, the lesson it produced, and how far it got next time.",
      },
      { property: "og:title", content: "The Graveyard — GRUE LAB" },
      {
        property: "og:description",
        content: "A timeline of tombstones: deaths get later, scores climb.",
      },
    ],
  }),
  component: Graveyard,
});

const RUN_ID = "harness-1";

function Graveyard() {
  const [openLife, setOpenLife] = useState<number | null>(null);
  const livesQ = useQuery({ queryKey: ["lives", RUN_ID], queryFn: () => api.listLives(RUN_ID) });

  if (livesQ.isLoading) return <LoadingState label="Exhuming lives" />;
  if (livesQ.isError) return <ErrorState error={livesQ.error} onRetry={() => livesQ.refetch()} />;
  const lives = livesQ.data ?? [];
  if (lives.length === 0) return <EmptyState label="No deaths recorded yet" />;

  const maxScore = Math.max(...lives.map((l) => l.score));

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl tracking-widest text-foreground uppercase">
          The Graveyard
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Ten lives, in order. Each tombstone carries the rule its death produced.
        </p>
      </header>

      <div className="flex gap-4 overflow-x-auto pb-4">
        {lives.map((l, i) => (
          <motion.button
            key={l.life}
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            onClick={() => setOpenLife(l.life)}
            className="panel min-w-[220px] cursor-pointer p-4 text-left transition-colors hover:border-lesson/60"
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs tracking-widest text-muted-foreground">
                LIFE {l.life}
              </span>
              <Skull className="h-4 w-4 text-baseline" />
            </div>
            <p className="phosphor-text mt-3 font-mono text-4xl">{l.score}</p>
            <div className="mt-2 h-1.5 w-full overflow-hidden rounded bg-muted">
              <div
                className="h-full bg-harness"
                style={{ width: `${(l.score / maxScore) * 100}%` }}
              />
            </div>
            <p className="mt-3 text-sm text-foreground">{l.death_cause}</p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">survived {l.moves} moves</p>
            {l.rules_learned.length > 0 && (
              <p className="mt-3 font-mono text-xs text-lesson">
                → produced {l.rules_learned.join(", ")}
              </p>
            )}
          </motion.button>
        ))}
      </div>

      <Dialog open={openLife !== null} onOpenChange={(o) => !o && setOpenLife(null)}>
        <DialogContent className="max-h-[80vh] max-w-2xl overflow-y-auto">
          {openLife !== null && <LifeDetail life={openLife} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function LifeDetail({ life }: { life: number }) {
  const movesQ = useQuery({
    queryKey: ["moves", RUN_ID, life],
    queryFn: () => api.listMoves(RUN_ID, life),
  });
  const refQ = useQuery({
    queryKey: ["reflection", RUN_ID, life],
    queryFn: () => api.getReflection(RUN_ID, life),
  });

  return (
    <>
      <DialogHeader>
        <DialogTitle className="font-mono tracking-widest uppercase">
          Life {life} · final moments
        </DialogTitle>
      </DialogHeader>

      {refQ.isLoading ? (
        <LoadingState label="Loading reflection" />
      ) : refQ.isError ? (
        <ErrorState error={refQ.error} onRetry={() => refQ.refetch()} />
      ) : (
        refQ.data && (
          <div className="rounded-lg border border-lesson/40 p-4">
            <p className="panel-title">Reflection</p>
            <p className="mt-2 text-sm text-foreground">
              <span className="text-muted-foreground">Cause: </span>
              {refQ.data.cause}
            </p>
            <p className="mt-1 text-sm text-foreground">
              <span className="text-muted-foreground">Effect: </span>
              {refQ.data.effect}
            </p>
            <p className="mt-3 text-base text-lesson">{refQ.data.lesson_text}</p>
          </div>
        )
      )}

      <p className="panel-title mt-4">Last 10 moves</p>
      {movesQ.isLoading ? (
        <LoadingState label="Loading transcript" />
      ) : movesQ.isError ? (
        <ErrorState error={movesQ.error} onRetry={() => movesQ.refetch()} />
      ) : (
        <ol className="crt panel space-y-2 p-4 font-mono text-sm">
          {(movesQ.data ?? []).slice(-10).map((m) => (
            <li key={m.move} className="relative z-10">
              <span className="text-muted-foreground">{m.move}. </span>
              <span className="phosphor-text">{m.room}</span>
              <span className="text-foreground"> &gt; {m.command}</span>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}
