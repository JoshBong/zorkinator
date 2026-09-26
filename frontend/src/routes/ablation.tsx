import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/api/client";
import { ErrorState, LoadingState } from "@/components/states";
import { Switch } from "@/components/ui/switch";
import type { EvalCondition, HarnessConfig } from "@/api/types";

export const Route = createFileRoute("/ablation")({
  head: () => ({
    meta: [
      { title: "Ablation Lab — GRUE LAB" },
      {
        name: "description",
        content:
          "Flip reflection, rules, memory and guardrails on or off and see the recorded result.",
      },
      { property: "og:title", content: "Ablation Lab — GRUE LAB" },
      {
        property: "og:description",
        content: "Which part of the harness actually drives the improvement?",
      },
    ],
  }),
  component: AblationLab,
});

const KEYS: (keyof HarnessConfig)[] = ["reflection", "rules", "memory", "guardrails"];

function sameConfig(a: HarnessConfig, b: HarnessConfig) {
  return KEYS.every((k) => a[k] === b[k]);
}

function AblationLab() {
  const [config, setConfig] = useState<HarnessConfig>({
    reflection: true,
    rules: true,
    memory: true,
    guardrails: true,
  });

  const summaryQ = useQuery({ queryKey: ["eval"], queryFn: () => api.getEvalSummary() });

  if (summaryQ.isLoading) return <LoadingState label="Loading ablations" />;
  if (summaryQ.isError)
    return <ErrorState error={summaryQ.error} onRetry={() => summaryQ.refetch()} />;

  const conditions = summaryQ.data?.conditions ?? [];
  const full = conditions.find((c) => c.condition === "harness");
  const match: EvalCondition | undefined = conditions.find((c) => sameConfig(c.config, config));
  const drop = full && match ? full.avg_score - match.avg_score : 0;

  const noReflection = conditions.find((c) => c.condition === "No reflection");
  const baseline = conditions.find((c) => c.condition === "baseline");
  const share =
    full && baseline && noReflection
      ? Math.round(
          ((full.avg_score - noReflection.avg_score) / (full.avg_score - baseline.avg_score)) * 100,
        )
      : null;

  const ranked = [...conditions]
    .filter((c) => c.condition !== "baseline")
    .sort((a, b) => b.avg_score - a.avg_score)
    .map((c) => ({ name: c.condition, score: c.avg_score, full: c.condition === "harness" }));

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl tracking-widest uppercase">Ablation Lab</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Turn pieces of the harness off and watch the recorded outcome change.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {KEYS.map((k) => (
          <label
            key={k}
            className={`panel flex cursor-pointer items-center justify-between p-5 text-left transition-colors ${
              config[k] ? "border-harness/60" : "border-border"
            }`}
          >
            <span
              className={`font-mono text-sm tracking-widest uppercase ${
                config[k] ? "text-harness" : "text-muted-foreground"
              }`}
            >
              {k}
            </span>
            <Switch
              checked={config[k]}
              onCheckedChange={() => setConfig((c) => ({ ...c, [k]: !c[k] }))}
            />
          </label>
        ))}
      </div>

      {match ? (
        <motion.div
          key={match.condition}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="grid gap-4 lg:grid-cols-[320px_1fr]"
        >
          <div className="panel space-y-4 p-5">
            <div>
              <p className="panel-title">Configuration</p>
              <p className="mt-1 font-mono text-lg text-foreground">{match.condition}</p>
            </div>
            <div>
              <p className="panel-title">Average score</p>
              <p className="mt-1 font-mono text-5xl text-harness">{match.avg_score}</p>
            </div>
            <div>
              <p className="panel-title">Drop from full harness</p>
              <p
                className={`mt-1 font-mono text-2xl ${drop > 0 ? "text-baseline" : "text-harness"}`}
              >
                {drop > 0 ? `-${drop}` : "—"}
              </p>
            </div>
            <p className="font-mono text-[11px] text-muted-foreground">n = {match.n_runs} run(s)</p>
          </div>

          <div className="panel p-4">
            <p className="panel-title mb-3">Score per life for this configuration</p>
            <div className="h-[240px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={match.per_life}>
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                  <XAxis dataKey="life" stroke="var(--color-muted-foreground)" fontSize={11} />
                  <YAxis stroke="var(--color-muted-foreground)" fontSize={11} />
                  <Tooltip
                    contentStyle={{
                      background: "var(--color-surface)",
                      border: "1px solid var(--color-border)",
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                  />
                  <Line
                    dataKey="mean"
                    stroke="var(--color-harness)"
                    strokeWidth={2.5}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </motion.div>
      ) : (
        <div className="panel p-10 text-center">
          <p className="font-mono text-sm tracking-widest text-guard uppercase">Not run</p>
          <p className="mt-2 text-sm text-muted-foreground">
            This combination was never evaluated. Flip a switch back to see recorded results.
          </p>
        </div>
      )}

      <section className="panel p-4">
        <p className="panel-title mb-3">All tested configurations</p>
        <div className="h-[300px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={ranked} layout="vertical" margin={{ left: 40 }}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
              <XAxis type="number" stroke="var(--color-muted-foreground)" fontSize={11} />
              <YAxis
                type="category"
                dataKey="name"
                width={120}
                stroke="var(--color-muted-foreground)"
                fontSize={11}
              />
              <Tooltip
                cursor={{ fill: "var(--color-secondary)" }}
                contentStyle={{
                  background: "var(--color-surface)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 8,
                  fontSize: 12,
                }}
              />
              <Bar dataKey="score" radius={4}>
                {ranked.map((r) => (
                  <Cell
                    key={r.name}
                    fill={r.full ? "var(--color-harness)" : "var(--color-phosphor-dim)"}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        {share !== null && (
          <p className="mt-3 font-mono text-sm text-lesson">
            Reflection drives {share}% of the improvement over baseline.
          </p>
        )}
      </section>
    </div>
  );
}
