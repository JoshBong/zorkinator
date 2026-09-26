import { motion } from "motion/react";
import { useMemo } from "react";
import type { RunMap } from "@/api/types";
import { layoutMap } from "@/lib/map-layout";

/** How much a room's brightness drops per game since it was first discovered. Floors at 0.45
 * so nothing already-known ever fully disappears — it just fades into "older territory". */
const AGE_FADE_PER_GAME = 0.15;
const AGE_FADE_FLOOR = 0.45;

const ICON_SIZE = 36;
const BADGE_RADIUS = 24;

const ROOM_TYPE_ICON_SRC: [RegExp, string][] = [
  [/house/i, "/room-icons/house.png"],
  [/kitchen/i, "/room-icons/kitchen.png"],
  [/living room|parlor|lounge/i, "/room-icons/living-room.png"],
  [/attic|loft/i, "/room-icons/attic.png"],
  [/gallery|museum/i, "/room-icons/gallery.png"],
  [/studio/i, "/room-icons/studio.png"],
  [/temple|altar|shrine|chapel/i, "/room-icons/temple.png"],
  [/troll|thief|cyclops|dragon|guard/i, "/room-icons/monster.png"],
  [/river|reservoir|stream|lake|water|dam|reserv/i, "/room-icons/water.png"],
  [/forest|clearing|path|glade|tree|grove|garden/i, "/room-icons/forest.png"],
  [/cellar|dungeon|crypt|tomb|maze|cave|grotto|pit|shaft/i, "/room-icons/underground.png"],
  [/passage|corridor|tunnel|hall/i, "/room-icons/passage.png"],
];
const GENERIC_LIGHT_ICON = "/room-icons/generic-room.png";
const GENERIC_DARK_ICON = "/room-icons/generic-dark.png";

function roomIconSrc(name: string, dark: boolean): string {
  for (const [pattern, src] of ROOM_TYPE_ICON_SRC) {
    if (pattern.test(name)) return src;
  }
  return dark ? GENERIC_DARK_ICON : GENERIC_LIGHT_ICON;
}

/** The icon itself stays full-color (it's cartoon art, not a recolorable single-tint glyph) —
 * state is instead shown by a colored badge ring behind it. */
function badgeColorVar(isGrave: boolean, isCurrent: boolean) {
  if (isGrave) return "var(--baseline)";
  if (isCurrent) return "var(--harness)";
  return "var(--phosphor-dim)";
}

export function MapGraph({
  map,
  visited,
  currentRoom,
  previousRoom,
  currentGame,
  startRoom,
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
  /** The room the agent actually starts each game in — roots the layout there so the map grows
   * left (entrance) to right (deepest exploration) instead of an arbitrary API order. */
  startRoom?: string | undefined;
  className?: string;
}) {
  const { positions, labelPosition, width, height } = useMemo(
    () => layoutMap(map, startRoom),
    [map, startRoom],
  );
  const current = currentRoom ? positions.get(currentRoom) : undefined;
  const roomsByName = useMemo(() => new Map(map.rooms.map((r) => [r.name, r])), [map.rooms]);
  // Some real chains report the same edge more than once (e.g. walked both directions on
  // different moves); de-dupe by room pair regardless of direction before rendering.
  const edges = useMemo(() => {
    const seenPairs = new Set<string>();
    return map.edges.filter((e) => {
      const key = [e.from, e.to].sort().join("::");
      if (seenPairs.has(key)) return false;
      seenPairs.add(key);
      return true;
    });
  }, [map.edges]);

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
      <div className="flex-1 overflow-hidden p-2" style={{ perspective: 1400 }}>
        {/* A slow, continuous 3D drift on the whole map plane — like a camera slowly orbiting
            a table-top diorama — layered underneath the per-move zoom-toward-the-agent below. */}
        <motion.div
          className="h-full w-full"
          style={{ transformStyle: "preserve-3d" }}
          animate={{ rotateX: [4, -3, 4], rotateY: [-6, 6, -6] }}
          transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }}
        >
          <svg viewBox={`0 0 ${width} ${height}`} className="h-full w-full overflow-visible">
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
              {/* Only ever draws rooms/edges the agent has actually reached — nothing is
                  pre-drawn faintly in the background, so the map genuinely builds itself move by
                  move instead of "revealing" a graph that was secretly there the whole time. */}
              {edges.map((e) => {
                const a = positions.get(e.from);
                const b = positions.get(e.to);
                if (!a || !b) return null;
                if (!knownByNow(e.from) || !knownByNow(e.to)) return null;
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
                    className={justWalked ? "text-harness" : "text-phosphor-dim"}
                    strokeWidth={justWalked ? 2.8 : 1.6}
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

              {map.rooms.map((r) => {
                const p = positions.get(r.name);
                if (!p) return null;
                if (!knownByNow(r.name)) return null;
                const isCurrent = r.name === currentRoom;
                const deathsSoFar = r.death_lives.filter((life) => life <= currentGame);
                const isGrave = deathsSoFar.length > 0;
                const age = Math.max(0, currentGame - r.first_seen_life);
                const ageOpacity = Math.max(AGE_FADE_FLOOR, 1 - age * AGE_FADE_PER_GAME);
                const badgeColor = badgeColorVar(isGrave, isCurrent);
                return (
                  <motion.g
                    key={r.name}
                    initial={{ opacity: 0, scale: 0.3 }}
                    animate={{ opacity: ageOpacity, scale: 1 }}
                    transition={{ type: "spring", stiffness: 220, damping: 18 }}
                    style={{ transformOrigin: `${p.x}px ${p.y}px` }}
                  >
                    {r.dark && <circle cx={p.x} cy={p.y} r={30} className="fill-foreground/5" />}
                    {isCurrent && (
                      <motion.circle
                        cx={p.x}
                        cy={p.y}
                        className="fill-none stroke-harness"
                        strokeWidth={2}
                        initial={{ r: BADGE_RADIUS, opacity: 0.8 }}
                        animate={{
                          r: [BADGE_RADIUS, BADGE_RADIUS + 18, BADGE_RADIUS],
                          opacity: [0.8, 0, 0.8],
                        }}
                        transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut" }}
                      />
                    )}
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={BADGE_RADIUS}
                      fillOpacity={0.3}
                      strokeWidth={1.5}
                      style={{ fill: badgeColor, stroke: badgeColor }}
                    />
                    <image
                      href={roomIconSrc(r.name, r.dark)}
                      x={p.x - ICON_SIZE / 2}
                      y={p.y - ICON_SIZE / 2}
                      width={ICON_SIZE}
                      height={ICON_SIZE}
                    />
                    {isGrave && (
                      <text
                        x={p.x + BADGE_RADIUS}
                        y={p.y - BADGE_RADIUS}
                        fontSize="16"
                        className="fill-baseline"
                      >
                        ☠
                      </text>
                    )}
                    <text
                      x={p.x}
                      y={
                        labelPosition.get(r.name) === "above"
                          ? p.y - BADGE_RADIUS - 12
                          : p.y + BADGE_RADIUS + 18
                      }
                      textAnchor="middle"
                      fontSize="14"
                      className="fill-muted-foreground font-mono"
                    >
                      {r.name}
                    </text>
                  </motion.g>
                );
              })}
            </motion.g>
          </svg>
        </motion.div>
      </div>
    </div>
  );
}
