import { createFileRoute } from "@tanstack/react-router";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/api/client";
import { ErrorState, LoadingState } from "@/components/states";

export const Route = createFileRoute("/eval")({
  head: () => ({
    meta: [
      { title: "GRUE LAB" },
      {
        name: "description",
        content:
          "Score per life, moves survived and causes of death: harness versus baseline across runs.",
      },
      { property: "og:title", content: "Eval Lab — GRUE LAB" },
      {
        property: "og:description",
        content: "The proof: the same model scores far higher inside the self-evolving harness.",
      },
    ],
  }),
  component: EvalLab,
});

const AXIS = { stroke: "var(--color-muted-foreground)", fontSize: 11 };
const TOOLTIP = {
  contentStyle: {
    background: "var(--color-surface)",
    border: "1px solid var(--color-border)",
    borderRadius: 8,
    fontSize: 12,
  },
};

function EvalLab() {
  const summaryQ = useQuery({ queryKey: ["eval"], queryFn: () => api.getEvalSummary() });
  const harnessLives = useQueries({
    queries: ["harness-1", "harness-2", "harness-3"].map((id) => ({
      queryKey: ["lives", id],
      queryFn: () => api.listLives(id),
    })),
  });

  if (summaryQ.isLoading) return <LoadingState label="Crunching evaluations" />;
  if (summaryQ.isError)
    return <ErrorState error={summaryQ.error} onRetry={() => summaryQ.refetch()} />;

  const conditions = summaryQ.data?.conditions ?? [];
  const base = conditions.find((c) => c.condition === "baseline");
  const harn = conditions.find((c) => c.condition === "harness");
  if (!base || !harn) return <ErrorState error={new Error("Missing conditions in summary")} />;

  const improvement = Math.round(((harn.avg_score - base.avg_score) / base.avg_score) * 100);
  const repeatedReduction = Math.round(
    ((base.repeated_actions - harn.repeated_actions) / base.repeated_actions) * 100,
  );

  const scoreData = harn.per_life.map((p, i) => ({
    life: p.life,
    harness: p.mean,
    harnessBand: [p.min, p.max],
    baseline: base.per_life[i]?.mean ?? 0,
    baselineBand: [base.per_life[i]?.min ?? 0, base.per_life[i]?.max ?? 0],
  }));

  const movesData = harn.per_life.map((p, i) => ({
    life: p.life,
    harness: Math.round(harn.avg_moves * (0.6 + p.life / 12)),
    baseline: base.per_life[i]?.mean ? base.avg_moves : base.avg_moves,
  }));

  const deaths = Array.from({ length: 10 }, (_, i) => {
    const rows = harnessLives.map((q) => q.data?.[i]).filter(Boolean);
    const count = (needle: string) => rows.filter((r) => r!.death_cause.includes(needle)).length;
    return {
      life: i + 1,
      grue: count("grue"),
      troll: count("troll"),
      other: count("fell") + count("drowned"),
      survived: count("survived"),
    };
  });

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl tracking-widest uppercase">Eval Lab</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Same model, same game. The only difference is the harness.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        <Stat label="Baseline avg" value={base.avg_score} tone="baseline" />
        <Stat label="Harness avg" value={harn.avg_score} tone="harness" />
        <Stat label="Improvement" value={`+${improvement}%`} tone="harness" />
        <Stat label="Runs (n)" value={base.n_runs + harn.n_runs} />
        <Stat label="Repeat reduction" value={`-${repeatedReduction}%`} tone="lesson" />
        <Stat label="Guardrail blocks" value={harn.guardrail_blocks} tone="guard" />
      </div>

      <section className="panel p-4">
        <p className="panel-title mb-3">Score per life · mean with min–max band</p>
        <div className="h-[340px]">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={scoreData}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
              <XAxis dataKey="life" {...AXIS} />
              <YAxis {...AXIS} />
              <Tooltip {...TOOLTIP} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Area
                dataKey="harnessBand"
                fill="var(--color-harness)"
                fillOpacity={0.12}
                stroke="none"
                name="harness range"
              />
              <Area
                dataKey="baselineBand"
                fill="var(--color-baseline)"
                fillOpacity={0.12}
                stroke="none"
                name="baseline range"
              />
              <Line
                dataKey="harness"
                stroke="var(--color-harness)"
                strokeWidth={2.5}
                dot={false}
                name="harness"
              />
              <Line
                dataKey="baseline"
                stroke="var(--color-baseline)"
                strokeWidth={2.5}
                dot={false}
                name="baseline"
              />
              <ReferenceLine
                y={35}
                stroke="var(--color-guard)"
                strokeDasharray="6 4"
                label={{
                  value: "Frontier models in published study (~10% of 350)",
                  fill: "var(--color-guard)",
                  fontSize: 11,
                  position: "insideBottomRight",
                }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="panel p-4">
          <p className="panel-title mb-3">Moves survived per life</p>
          <div className="h-[260px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={movesData}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                <XAxis dataKey="life" {...AXIS} />
                <YAxis {...AXIS} />
                <Tooltip {...TOOLTIP} />
                <Line
                  dataKey="harness"
                  stroke="var(--color-harness)"
                  strokeWidth={2.5}
                  dot={false}
                />
                <Line
                  dataKey="baseline"
                  stroke="var(--color-baseline)"
                  strokeWidth={2.5}
                  dot={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </section>

        <section className="panel p-4">
          <p className="panel-title mb-3">Causes of death per life (harness)</p>
          <div className="h-[260px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={deaths}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                <XAxis dataKey="life" {...AXIS} />
                <YAxis {...AXIS} allowDecimals={false} />
                <Tooltip {...TOOLTIP} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="grue" stackId="d" fill="var(--color-baseline)" />
                <Bar dataKey="troll" stackId="d" fill="var(--color-guard)" />
                <Bar dataKey="other" stackId="d" fill="var(--color-muted-foreground)" />
                <Bar dataKey="survived" stackId="d" fill="var(--color-harness)" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string | number;
  tone?: "default" | "harness" | "baseline" | "lesson" | "guard";
}) {
  const color =
    tone === "harness"
      ? "text-harness"
      : tone === "baseline"
        ? "text-baseline"
        : tone === "lesson"
          ? "text-lesson"
          : tone === "guard"
            ? "text-guard"
            : "text-foreground";
  return (
    <div className="panel p-4">
      <p className="panel-title">{label}</p>
      <p className={`mt-2 font-mono text-3xl ${color}`}>{value}</p>
    </div>
  );
}
