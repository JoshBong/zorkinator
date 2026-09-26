import type { RunMap } from "@/api/types";

export interface LaidOutRoom {
  name: string;
  x: number;
  y: number;
}

/** Deterministic layered (BFS) layout — no graph library needed.
 *
 * `startRoom` roots the layout at the game's actual entrance so it grows left (entrance) to
 * right (deepest exploration). Without it the root falls back to whichever room the API
 * happened to list first — for real chains that's Mongo's write order, not the entrance, so
 * the map could render backwards (the entrance shown deep on the right). Falls back to the
 * room first seen earliest, then to array order, if `startRoom` isn't in this map. */
export function layoutMap(map: RunMap, startRoom?: string, width = 900, height = 320) {
  const adjacency = new Map<string, string[]>();
  map.rooms.forEach((r) => adjacency.set(r.name, []));
  map.edges.forEach((e) => {
    adjacency.get(e.from)?.push(e.to);
    adjacency.get(e.to)?.push(e.from);
  });

  const depth = new Map<string, number>();
  const order: string[] = [];
  const earliestSeen = [...map.rooms].sort((a, b) => a.first_seen_life - b.first_seen_life)[0]
    ?.name;
  const root =
    (startRoom && adjacency.has(startRoom) ? startRoom : undefined) ??
    earliestSeen ??
    map.rooms[0]?.name;
  if (root) {
    depth.set(root, 0);
    const queue = [root];
    while (queue.length) {
      const cur = queue.shift()!;
      order.push(cur);
      for (const next of adjacency.get(cur) ?? []) {
        if (!depth.has(next)) {
          depth.set(next, (depth.get(cur) ?? 0) + 1);
          queue.push(next);
        }
      }
    }
  }
  map.rooms.forEach((r) => {
    if (!depth.has(r.name)) {
      depth.set(r.name, 0);
      order.push(r.name);
    }
  });

  const byDepth = new Map<number, string[]>();
  order.forEach((name) => {
    const d = depth.get(name) ?? 0;
    byDepth.set(d, [...(byDepth.get(d) ?? []), name]);
  });

  const maxDepth = Math.max(1, ...byDepth.keys());
  const positions = new Map<string, LaidOutRoom>();
  // Two different collisions to avoid: rooms sharing a depth (e.g. two exits off the same
  // room) stack vertically close together, so within such a group labels point outward (top
  // one up, bottom one down) instead of both the same way. Lone rooms in a depth have no
  // vertical neighbor, but their labels can still run into a horizontally adjacent column's —
  // alternating by column index keeps consecutive columns from picking the same side.
  const labelPosition = new Map<string, "above" | "below">();
  byDepth.forEach((names, d) => {
    names.forEach((name, i) => {
      const x = (d / maxDepth) * (width - 120) + 60;
      const y = ((i + 1) / (names.length + 1)) * (height - 60) + 20;
      positions.set(name, { name, x, y });
      const pos =
        names.length > 1
          ? i < names.length / 2
            ? "above"
            : "below"
          : d % 2 === 0
            ? "below"
            : "above";
      labelPosition.set(name, pos);
    });
  });

  return { positions, labelPosition, width, height };
}
