import { AnimatePresence, motion } from "motion/react";
import { Brain, ShieldAlert, Sparkles } from "lucide-react";
import type { Move } from "@/api/types";

export function MindPanel({
  move,
  className = "",
}: {
  move?: Move | undefined;
  className?: string;
}) {
  return (
    <div className={`panel flex flex-col overflow-hidden ${className}`}>
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <Brain className="h-3.5 w-3.5 text-lesson" />
        <span className="panel-title">The Agent&apos;s Mind</span>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <section>
          <p className="panel-title mb-2">Retrieved from Atlas (vector search)</p>
          {move && move.retrieved_rules.length > 0 ? (
            <ul className="space-y-2.5">
              {move.retrieved_rules.map((r) => (
                <motion.li
                  key={`${r.rule_id}-${r.version}`}
                  initial={{ opacity: 0, x: 8 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="rounded-md bg-surface-2 p-2.5"
                >
                  <div className="flex items-center justify-between font-mono text-[11px] text-muted-foreground">
                    <span>
                      {r.rule_id} · v{r.version}
                    </span>
                    <span className="text-lesson">{r.similarity.toFixed(2)}</span>
                  </div>
                  <p className="mt-1 text-sm text-foreground">{r.text}</p>
                  <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-muted">
                    <motion.div
                      className="h-full bg-lesson"
                      initial={{ width: 0 }}
                      animate={{ width: `${r.similarity * 100}%` }}
                      transition={{ duration: 0.4 }}
                    />
                  </div>
                </motion.li>
              ))}
            </ul>
          ) : (
            <p className="font-mono text-xs text-muted-foreground">
              No memory retrieved — agent is running blind.
            </p>
          )}
        </section>

        <AnimatePresence>
          {move?.guardrail?.blocked && (
            <motion.div
              key={move.move}
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              className="guard-flash glow-guard rounded-md border border-guard/60 p-3"
            >
              <div className="flex items-center gap-2 font-mono text-xs tracking-widest text-guard uppercase">
                <ShieldAlert className="h-4 w-4" /> Blocked
              </div>
              <p className="mt-1.5 font-mono text-sm text-foreground">
                {move.guardrail.original_command.toUpperCase()}
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                violates {move.guardrail.rule_id}: never drop your light source
              </p>
              <p className="mt-2 font-mono text-sm text-harness">
                → {move.guardrail.replacement_command}
              </p>
            </motion.div>
          )}
        </AnimatePresence>

        <section>
          <p className="panel-title mb-2">Command chosen</p>
          <div className="flex items-center gap-2 rounded-md border border-harness/40 bg-surface-2 p-3">
            <Sparkles className="h-4 w-4 text-harness" />
            <span className="font-mono text-base text-harness">{move?.command ?? "—"}</span>
          </div>
        </section>
      </div>
    </div>
  );
}
