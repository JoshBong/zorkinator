import type { RunMap } from "@/api/types";

export interface LaidOutRoom {
  name: string;
  x: number;
  y: number;
}

/** Deterministic layered (BFS) layout — no graph library needed. */
export function layoutMap(map: RunMap, width = 900, height = 220) {
  const adjacency = new Map<string, string[]>();
  map.rooms.forEach((r) => adjacency.set(r.name, []));
  map.edges.forEach((e) => {
    adjacency.get(e.from)?.push(e.to);
    adjacency.get(e.to)?.push(e.from);
  });

  const depth = new Map<string, number>();
  const order: string[] = [];
  const root = map.rooms[0]?.name;
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
  byDepth.forEach((names, d) => {
    names.forEach((name, i) => {
      const x = (d / maxDepth) * (width - 120) + 60;
      const y = ((i + 1) / (names.length + 1)) * (height - 60) + 20;
      positions.set(name, { name, x, y });
    });
  });

  return { positions, width, height };
}
