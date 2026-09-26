import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { motion } from "motion/react";

import { api } from "@/api/client";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import type { Rule, RuleType } from "@/api/types";

export const Route = createFileRoute("/rulebook")({
  head: () => ({
    meta: [
      { title: "The Rulebook — GRUE LAB" },
      {
        name: "description",
        content:
          "Versioned rules, guardrails and memories the agent wrote for itself, with diffs and lineage.",
      },
      { property: "og:title", content: "The Rulebook — GRUE LAB" },
      {
        property: "og:description",
        content: "Every rule version, the death that created it, and its score impact.",
      },
    ],
  }),
  component: Rulebook,
});

const FILTERS: (RuleType | "all")[] = ["all", "rule", "guardrail", "memory"];

const STATUS_STYLE: Record<string, string> = {
  active: "bg-harness/15 text-harness",
  superseded: "bg-muted text-muted-foreground",
  rolled_back: "bg-baseline/15 text-baseline",
};

function diffWords(prev: string, next: string) {
  const a = prev.split(" ");
  const b = next.split(" ");
  const removed = a.filter((w) => !b.includes(w));
  const added = b.filter((w) => !a.includes(w));
  return { removed, added };
}

function Rulebook() {
  const [filter, setFilter] = useState<RuleType | "all">("all");
  const rulesQ = useQuery({ queryKey: ["rules"], queryFn: () => api.listRules() });

  const grouped = useMemo(() => {
    const map = new Map<string, Rule[]>();
    (rulesQ.data ?? [])
      .filter((r) => filter === "all" || r.type === filter)
      .forEach((r) => map.set(r.rule_id, [...(map.get(r.rule_id) ?? []), r]));
    map.forEach((v) => v.sort((x, y) => x.version - y.version));
    return [...map.entries()];
  }, [rulesQ.data, filter]);

  if (rulesQ.isLoading) return <LoadingState label="Reading Atlas" />;
  if (rulesQ.isError) return <ErrorState error={rulesQ.error} onRetry={() => rulesQ.refetch()} />;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-mono text-2xl tracking-widest uppercase">The Rulebook</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Everything the agent learned, versioned like code.
          </p>
        </div>
        <div className="flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-md px-3 py-1.5 font-mono text-xs uppercase ${
                filter === f
                  ? "bg-harness/20 text-harness"
                  : "text-muted-foreground hover:bg-secondary"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </header>

      {grouped.length === 0 ? (
        <EmptyState label="No rules of this type yet" />
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {grouped.map(([ruleId, versions], gi) => (
            <motion.article
              key={ruleId}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: gi * 0.05 }}
              className="panel space-y-4 p-4"
            >
              <div className="flex items-center justify-between">
                <h2 className="font-mono text-sm tracking-widest text-foreground">{ruleId}</h2>
                <span className="font-mono text-[10px] tracking-widest text-muted-foreground uppercase">
                  {versions[0]?.type}
                </span>
              </div>

              <div className="flex flex-wrap items-center gap-2 font-mono text-xs">
                {versions.map((v, i) => (
                  <span key={v.version} className="flex items-center gap-2">
                    {i > 0 && <span className="text-muted-foreground">→</span>}
                    <span className={`rounded px-2 py-0.5 ${STATUS_STYLE[v.status]}`}>
                      v{v.version}
                      {v.status === "rolled_back" ? " (rolled back)" : ""}
                    </span>
                  </span>
                ))}
                {versions.some((v) => v.status === "rolled_back") && (
                  <span className="text-muted-foreground">→ v2 restored</span>
                )}
              </div>

              <ul className="space-y-3">
                {versions.map((v, i) => {
                  const prev = versions[i - 1];
                  const diff = prev ? diffWords(prev.text, v.text) : null;
                  return (
                    <li key={v.version} className="rounded-md bg-surface-2 p-3">
                      <div className="flex items-center justify-between font-mono text-[11px] text-muted-foreground">
                        <span>v{v.version}</span>
                        <span className={v.score_impact >= 0 ? "text-harness" : "text-baseline"}>
                          {v.score_impact >= 0 ? "+" : ""}
                          {v.score_impact} pts
                        </span>
                      </div>
                      <p className="mt-1.5 text-sm text-foreground">{v.text}</p>
                      <p className="mt-2 font-mono text-[11px] text-muted-foreground">
                        learned from{" "}
                        <Link to="/graveyard" className="text-lesson underline underline-offset-2">
                          life {v.learned_from.life}, move {v.learned_from.move}
                        </Link>{" "}
                        · {v.learned_from.run_id}
                      </p>
                      {diff && (diff.added.length > 0 || diff.removed.length > 0) && (
                        <p className="mt-2 font-mono text-xs">
                          {diff.removed.map((w) => (
                            <span key={`r-${w}`} className="mr-1 text-baseline line-through">
                              {w}
                            </span>
                          ))}
                          {diff.added.map((w) => (
                            <span key={`a-${w}`} className="mr-1 text-harness">
                              +{w}
                            </span>
                          ))}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </motion.article>
          ))}
        </div>
      )}
    </div>
  );
}
