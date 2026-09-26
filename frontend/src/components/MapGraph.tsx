import { motion } from "motion/react";
import { useMemo } from "react";
import type { RunMap } from "@/api/types";
import { layoutMap } from "@/lib/map-layout";

export function MapGraph({
  map,
  visited,
  currentRoom,
  className = "",
}: {
  map: RunMap;
  visited: Set<string>;
  currentRoom?: string | undefined;
  className?: string;
}) {
  const { positions, width, height } = useMemo(() => layoutMap(map), [map]);

  return (
    <div className={`panel flex flex-col overflow-hidden ${className}`}>
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="panel-title">The Map</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {visited.size}/{map.rooms.length} rooms discovered
        </span>
      </div>
      <div className="flex-1 overflow-hidden p-2">
        <svg viewBox={`0 0 ${width} ${height}`} className="h-full w-full">
          {map.edges.map((e) => {
            const a = positions.get(e.from);
            const b = positions.get(e.to);
            if (!a || !b) return null;
            const on = visited.has(e.from) && visited.has(e.to);
            return (
              <motion.line
                key={`${e.from}-${e.to}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke="currentColor"
                className={on ? "text-phosphor-dim" : "text-border"}
                strokeWidth={on ? 1.6 : 1}
                strokeDasharray={on ? undefined : "3 4"}
                initial={{ pathLength: 0 }}
                animate={{ pathLength: 1 }}
                transition={{ duration: 0.6 }}
              />
            );
          })}

          {map.rooms.map((r, i) => {
            const p = positions.get(r.name);
            if (!p) return null;
            const seen = visited.has(r.name);
            const isCurrent = r.name === currentRoom;
            return (
              <g key={r.name} opacity={seen ? 1 : 0.22}>
                {r.dark && <circle cx={p.x} cy={p.y} r={18} className="fill-foreground/5" />}
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={9}
                  className={
                    isCurrent
                      ? "room-pulse fill-harness"
                      : r.dark
                        ? "fill-muted stroke-border"
                        : "fill-phosphor-dim"
                  }
                />
                {r.death_count > 0 && (
                  <text x={p.x + 10} y={p.y - 8} fontSize="12" className="fill-baseline">
                    ☠
                  </text>
                )}
                <text
                  x={p.x}
                  y={p.y + (i % 2 === 0 ? 26 : -18)}
                  textAnchor="middle"
                  fontSize="13"
                  className="fill-muted-foreground font-mono"
                >
                  {r.name}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
