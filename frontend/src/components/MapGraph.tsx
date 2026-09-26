import { motion } from "motion/react";
import { useMemo } from "react";
import type { RunMap } from "@/api/types";
import { layoutMap } from "@/lib/map-layout";

/** How much a room's brightness drops per game since it was first discovered. Floors at 0.45
 * so nothing already-known ever fully disappears — it just fades into "older territory". */
const AGE_FADE_PER_GAME = 0.15;
const AGE_FADE_FLOOR = 0.45;

export function MapGraph({
  map,
  visited,
  currentRoom,
  previousRoom,
  currentGame,
  className = "",
}: {
  map: RunMap;
  visited: Set<string>;
  currentRoom?: string | undefined;
  previousRoom?: string | undefined;
  /** The game/life currently being viewed. Rooms first seen in an earlier game are shown as
   * already-known (no need to wait for today's replay to reach them); rooms first seen later
   * than this stay hidden, so stepping G1 -> G10 reveals the map's real learning curve instead
   * of spoiling it. */
  currentGame: number;
  className?: string;
}) {
  const { positions, width, height } = useMemo(() => layoutMap(map), [map]);
  const current = currentRoom ? positions.get(currentRoom) : undefined;
  const roomsByName = useMemo(() => new Map(map.rooms.map((r) => [r.name, r])), [map.rooms]);

  const knownByNow = (name: string) => {
    const room = roomsByName.get(name);
    if (room && room.first_seen_life < currentGame) return true;
    return visited.has(name);
  };

  const discoveredCount = map.rooms.filter((r) => knownByNow(r.name)).length;

  return (
    <div className={`panel flex flex-col overflow-hidden ${className}`}>
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="panel-title">The Map</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {discoveredCount}/{map.rooms.length} rooms discovered
        </span>
      </div>
      <div className="flex-1 overflow-hidden p-2">
        <svg viewBox={`0 0 ${width} ${height}`} className="h-full w-full">
          {/* Camera drifts toward and gently zooms in on wherever the agent currently is. */}
          <motion.g
            animate={{ scale: current ? 1.18 : 1 }}
            transition={{ duration: 0.9, ease: "easeInOut" }}
            style={{
              transformOrigin: current
                ? `${current.x}px ${current.y}px`
                : `${width / 2}px ${height / 2}px`,
            }}
          >
            {map.edges.map((e) => {
              const a = positions.get(e.from);
              const b = positions.get(e.to);
              if (!a || !b) return null;
              const on = knownByNow(e.from) && knownByNow(e.to);
              const justWalked =
                (e.from === previousRoom && e.to === currentRoom) ||
                (e.to === previousRoom && e.from === currentRoom);
              return (
                <motion.line
                  key={`${e.from}-${e.to}`}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke="currentColor"
                  className={justWalked ? "text-harness" : on ? "text-phosphor-dim" : "text-border"}
                  strokeWidth={justWalked ? 2.8 : on ? 1.6 : 1}
                  strokeDasharray={on ? undefined : "3 4"}
                  initial={{ pathLength: 0 }}
                  animate={{ pathLength: 1, opacity: justWalked ? [1, 0.4, 1] : 1 }}
                  transition={
                    justWalked
                      ? { pathLength: { duration: 0.5 }, opacity: { duration: 0.9, repeat: 1 } }
                      : { duration: 0.6 }
                  }
                />
              );
            })}

            {map.rooms.map((r, i) => {
              const p = positions.get(r.name);
              if (!p) return null;
              const seen = knownByNow(r.name);
              const isCurrent = r.name === currentRoom;
              const deathsSoFar = r.death_lives.filter((life) => life <= currentGame);
              // Gated on `seen` too: a death can never predate its own room's discovery, but
              // this guards against that invariant breaking upstream (e.g. inconsistent data).
              const isGrave = seen && deathsSoFar.length > 0;
              const age = Math.max(0, currentGame - r.first_seen_life);
              const ageOpacity = Math.max(AGE_FADE_FLOOR, 1 - age * AGE_FADE_PER_GAME);
              return (
                // Keying on `seen` remounts the group the moment a room is first
                // discovered, retriggering the pop-in spring below.
                <motion.g
                  key={`${r.name}-${seen}`}
                  initial={seen ? { opacity: 0, scale: 0.3 } : { opacity: 0.22, scale: 1 }}
                  animate={{ opacity: seen ? ageOpacity : 0.22, scale: 1 }}
                  transition={{ type: "spring", stiffness: 260, damping: 18 }}
                >
                  {r.dark && <circle cx={p.x} cy={p.y} r={18} className="fill-foreground/5" />}
                  {isCurrent && (
                    <motion.circle
                      cx={p.x}
                      cy={p.y}
                      className="fill-none stroke-harness"
                      strokeWidth={2}
                      initial={{ r: 9, opacity: 0.8 }}
                      animate={{ r: [9, 24, 9], opacity: [0.8, 0, 0.8] }}
                      transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut" }}
                    />
                  )}
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={9}
                    className={
                      isGrave
                        ? "fill-baseline"
                        : isCurrent
                          ? "room-pulse fill-harness"
                          : r.dark
                            ? "fill-muted stroke-border"
                            : "fill-phosphor-dim"
                    }
                  />
                  {isGrave && (
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
                </motion.g>
              );
            })}
          </motion.g>
        </svg>
      </div>
    </div>
  );
}
