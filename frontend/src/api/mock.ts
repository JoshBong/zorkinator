import type {
  EvalSummary,
  HarnessConfig,
  Life,
  Move,
  Reflection,
  Rule,
  Run,
  RunMap,
} from "./types";

/* ------------------------------------------------------------------ */
/* deterministic PRNG (no module-scope randomness — worker safe)       */
/* ------------------------------------------------------------------ */
function seeded(seed: string) {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return () => {
    h += 0x6d2b79f5;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* ------------------------------------------------------------------ */
/* world                                                               */
/* ------------------------------------------------------------------ */
export const ROOMS: { name: string; dark: boolean; x: number; y: number }[] = [
  { name: "West of House", dark: false, x: 80, y: 60 },
  { name: "North of House", dark: false, x: 220, y: 30 },
  { name: "Behind House", dark: false, x: 350, y: 60 },
  { name: "Kitchen", dark: false, x: 350, y: 150 },
  { name: "Living Room", dark: false, x: 220, y: 150 },
  { name: "Attic", dark: true, x: 430, y: 215 },
  { name: "Cellar", dark: true, x: 220, y: 250 },
  { name: "Troll Room", dark: true, x: 90, y: 300 },
  { name: "East-West Passage", dark: true, x: 230, y: 350 },
  { name: "Round Room", dark: true, x: 360, y: 320 },
  { name: "Gallery", dark: true, x: 440, y: 400 },
  { name: "Studio", dark: true, x: 300, y: 440 },
];

const EDGES: [string, string, string][] = [
  ["West of House", "North of House", "north"],
  ["North of House", "Behind House", "east"],
  ["Behind House", "Kitchen", "west"],
  ["Kitchen", "Living Room", "west"],
  ["Kitchen", "Attic", "up"],
  ["Living Room", "Cellar", "down"],
  ["Cellar", "Troll Room", "south"],
  ["Troll Room", "East-West Passage", "east"],
  ["East-West Passage", "Round Room", "east"],
  ["Round Room", "Gallery", "southeast"],
  ["Gallery", "Studio", "west"],
];

const OBSERVATIONS: Record<string, string> = {
  "West of House":
    "A weathered white house sits behind an overgrown lawn. A mailbox leans by the path.",
  "North of House": "The north face of the house. Boarded windows refuse to give.",
  "Behind House": "A small window at the back of the house hangs slightly open.",
  Kitchen: "A cluttered kitchen. A sack sits on the table, and a dark stairway leads up.",
  "Living Room": "A trophy case dominates the wall. A worn rug covers part of the floor.",
  Attic: "Pitch black. You sense the shape of low rafters overhead.",
  Cellar: "Cold stone walls. The trapdoor above slams shut behind you.",
  "Troll Room": "A bare chamber littered with bones. Something breathes in the corner.",
  "East-West Passage": "A narrow corridor running east and west, damp underfoot.",
  "Round Room": "A circular chamber with exits in every direction, all identical.",
  Gallery: "Empty frames hang crooked on the walls. One painting survives.",
  Studio: "Soot streaks the walls. A narrow chimney rises out of sight.",
};

const DEATH_CAUSES = [
  "eaten by a grue",
  "killed by the troll",
  "drowned in the reservoir",
  "fell down the shaft",
  "eaten by a grue",
];

const COMMANDS = [
  "open mailbox",
  "north",
  "east",
  "open window",
  "enter house",
  "west",
  "take lamp",
  "turn on lamp",
  "move rug",
  "open trapdoor",
  "down",
  "south",
  "attack troll with sword",
  "east",
  "take painting",
  "up",
];

export const RULE_TEXTS: { rule_id: string; type: Rule["type"]; versions: string[] }[] = [
  {
    rule_id: "R-001",
    type: "rule",
    versions: [
      "Never enter a dark room without a lit lamp.",
      "Never enter a dark room without a lit lamp; turn the lamp on before descending.",
      "Never enter a dark room. Carry and light the brass lamp before any downward move.",
    ],
  },
  {
    rule_id: "R-002",
    type: "guardrail",
    versions: [
      "Never drop your light source.",
      "Never drop, give away, or store the lamp while underground.",
    ],
  },
  {
    rule_id: "R-003",
    type: "rule",
    versions: ["Arm yourself with the sword before entering the Troll Room."],
  },
  {
    rule_id: "R-004",
    type: "memory",
    versions: [
      "The trapdoor under the rug in the Living Room leads to the Cellar.",
      "The rug hides a trapdoor to the Cellar; it locks behind you on the way down.",
    ],
  },
  {
    rule_id: "R-005",
    type: "guardrail",
    versions: ["Do not repeat a command that produced no state change three times."],
  },
  {
    rule_id: "R-006",
    type: "rule",
    versions: ["Deposit treasures in the trophy case before exploring deeper."],
  },
];

/* ------------------------------------------------------------------ */
/* runs                                                                */
/* ------------------------------------------------------------------ */
const ABLATIONS: { id: string; label: string; config: HarnessConfig }[] = [
  {
    id: "abl-no-reflection",
    label: "No reflection",
    config: { reflection: false, rules: true, memory: true, guardrails: true },
  },
  {
    id: "abl-no-rules",
    label: "No rules",
    config: { reflection: true, rules: false, memory: true, guardrails: true },
  },
  {
    id: "abl-no-memory",
    label: "No memory",
    config: { reflection: true, rules: true, memory: false, guardrails: true },
  },
  {
    id: "abl-no-guardrails",
    label: "No guardrails",
    config: { reflection: true, rules: true, memory: true, guardrails: false },
  },
  {
    id: "abl-rules-only",
    label: "Rules only",
    config: { reflection: false, rules: true, memory: false, guardrails: false },
  },
];

export function getRuns(): Run[] {
  const runs: Run[] = [];
  for (let i = 1; i <= 3; i++) {
    runs.push({
      run_id: `baseline-${i}`,
      condition: "baseline",
      model: "gpt-5-mini",
      config: { reflection: false, rules: false, memory: false, guardrails: false },
      status: "done",
      lives_count: 10,
      created_at: `2026-09-2${i}T09:0${i}:00Z`,
    });
  }
  for (let i = 1; i <= 3; i++) {
    runs.push({
      run_id: `harness-${i}`,
      condition: "harness",
      model: "gpt-5-mini",
      config: { reflection: true, rules: true, memory: true, guardrails: true },
      status: i === 1 ? "running" : "done",
      lives_count: 10,
      created_at: `2026-09-2${i}T11:0${i}:00Z`,
    });
  }
  for (const a of ABLATIONS) {
    runs.push({
      run_id: a.id,
      condition: "ablation",
      model: "gpt-5-mini",
      config: a.config,
      status: "done",
      lives_count: 10,
      created_at: "2026-09-22T15:00:00Z",
    });
  }
  return runs;
}

function conditionOf(runId: string) {
  if (runId.startsWith("baseline")) return "baseline" as const;
  if (runId.startsWith("harness")) return "harness" as const;
  return "ablation" as const;
}

function ablationPenalty(runId: string) {
  switch (runId) {
    case "abl-no-reflection":
      return 0.24;
    case "abl-no-rules":
      return 0.48;
    case "abl-no-memory":
      return 0.68;
    case "abl-no-guardrails":
      return 0.74;
    case "abl-rules-only":
      return 0.42;
    default:
      return 1;
  }
}

export function lifeScore(runId: string, life: number): number {
  const rnd = seeded(`${runId}:${life}`);
  const cond = conditionOf(runId);
  if (cond === "baseline") return Math.round(20 + rnd() * 20);
  const growth = 18 + life * 12 + rnd() * 14;
  if (cond === "harness") return Math.round(growth);
  return Math.round(20 + (growth - 20) * ablationPenalty(runId));
}

/** Fixed move budget per game (outer loop) — same N for baseline and harness. */
export const MOVES_PER_GAME = 60;
export const GAMES_PER_RUN = 10;

export function lifeMoves(_runId: string, _life: number): number {
  return MOVES_PER_GAME;
}

export function getLives(runId: string): Life[] {
  const cond = conditionOf(runId);
  return Array.from({ length: 10 }, (_, i) => {
    const life = i + 1;
    const rnd = seeded(`${runId}:d:${life}`);
    const grueFade = cond === "baseline" ? 0 : life / 10;
    const cause =
      rnd() < 0.6 - grueFade * 0.5
        ? "eaten by a grue"
        : DEATH_CAUSES[Math.floor(rnd() * DEATH_CAUSES.length)]!;
    const learned =
      cond === "baseline" ? [] : life <= RULE_TEXTS.length ? [RULE_TEXTS[life - 1]!.rule_id] : [];
    return {
      life,
      score: lifeScore(runId, life),
      moves: lifeMoves(runId, life),
      death_cause: life === 10 && cond !== "baseline" ? "survived (run ended)" : cause,
      rules_learned: learned,
    };
  });
}

export function getMoves(runId: string, life: number): Move[] {
  const cond = conditionOf(runId);
  const total = lifeMoves(runId, life);
  const finalScore = lifeScore(runId, life);
  const rnd = seeded(`${runId}:mv:${life}`);
  const moves: Move[] = [];
  let room = ROOMS[0]!.name;
  let lastCommand = "";
  let repeatStreak = 0;

  for (let i = 1; i <= total; i++) {
    const roomIndex = Math.min(ROOMS.length - 1, Math.floor((i / total) * ROOMS.length));
    room = ROOMS[roomIndex]!.name;
    let command = COMMANDS[(i - 1) % COMMANDS.length]!;

    // baseline repeats failing actions
    let repeated = false;
    if (cond === "baseline" && rnd() < 0.28) {
      command = lastCommand || command;
      repeatStreak += 1;
      repeated = repeatStreak >= 1;
    } else {
      repeatStreak = 0;
    }

    const availableRules = RULE_TEXTS.slice(0, Math.min(RULE_TEXTS.length, life));
    const retrieved =
      cond === "baseline" || availableRules.length === 0
        ? []
        : availableRules.slice(0, 3).map((r, idx) => ({
            rule_id: r.rule_id,
            version: Math.min(r.versions.length, Math.max(1, life - idx)),
            text: r.versions[Math.min(r.versions.length - 1, Math.max(0, life - idx - 1))]!,
            similarity: Math.round((0.94 - idx * 0.11 - rnd() * 0.05) * 100) / 100,
          }));

    const blocks = cond !== "baseline" && i % 11 === 6;
    const guardrail = blocks
      ? {
          blocked: true,
          rule_id: "R-002",
          original_command: "drop lamp",
          replacement_command: "keep lamp / go north",
        }
      : null;

    moves.push({
      move: i,
      observation: OBSERVATIONS[room] ?? "The darkness presses in from all sides.",
      command: guardrail ? guardrail.replacement_command : command,
      room,
      score: Math.round((finalScore * i) / total),
      retrieved_rules: retrieved,
      guardrail,
      repeated_action: repeated,
    });
    lastCommand = command;
  }
  return moves;
}

export function getReflection(runId: string, life: number): Reflection {
  const lives = getLives(runId);
  const l = lives[life - 1]!;
  const rule = RULE_TEXTS[Math.min(RULE_TEXTS.length - 1, life - 1)]!;
  return {
    cause: `The agent moved into an unlit area on move ${l.moves - 2} while the lamp was off.`,
    effect: `${l.death_cause} at move ${l.moves}. Score frozen at ${l.score}.`,
    lesson_text: rule.versions[0]!,
    rule_id: rule.rule_id,
  };
}

export function getRules(): Rule[] {
  const out: Rule[] = [];
  RULE_TEXTS.forEach((r, ri) => {
    r.versions.forEach((text, vi) => {
      const isLast = vi === r.versions.length - 1;
      const rolledBack = r.rule_id === "R-001" && vi === 2;
      out.push({
        rule_id: r.rule_id,
        version: vi + 1,
        type: r.type,
        text,
        status: rolledBack ? "rolled_back" : isLast ? "active" : "superseded",
        learned_from: { run_id: "harness-1", life: ri + vi + 1, move: 40 + ri * 3 + vi * 5 },
        score_impact: Math.round((rolledBack ? -8 : 6 + ri * 2 + vi * 4) * 10) / 10,
        parent_version: vi === 0 ? null : vi,
        created_at: `2026-09-2${Math.min(3, 1 + vi)}T1${ri}:0${vi}:00Z`,
      });
    });
  });
  // R-001 v2 restored after rollback
  const restored = out.find((r) => r.rule_id === "R-001" && r.version === 2);
  if (restored) restored.status = "active";
  return out;
}

export function getMap(runId: string): RunMap {
  const lives = getLives(runId);
  return {
    rooms: ROOMS.map((r, i) => ({
      name: r.name,
      dark: r.dark,
      first_seen_life: Math.max(1, Math.ceil((i + 1) / 2)),
      death_count: r.dark ? lives.filter((l) => l.death_cause.includes("grue")).length % 3 : 0,
    })),
    edges: EDGES.map(([from, to, direction]) => ({ from, to, direction })),
  };
}

export function getEvalSummary(): EvalSummary {
  const build = (label: string, runIds: string[], config: HarnessConfig) => {
    const per_life = Array.from({ length: 10 }, (_, i) => {
      const scores = runIds.map((r) => lifeScore(r, i + 1));
      return {
        life: i + 1,
        mean: Math.round(scores.reduce((a, b) => a + b, 0) / scores.length),
        min: Math.min(...scores),
        max: Math.max(...scores),
      };
    });
    const all = runIds.flatMap((r) => Array.from({ length: 10 }, (_, i) => lifeScore(r, i + 1)));
    const allMoves = runIds.flatMap((r) =>
      Array.from({ length: 10 }, (_, i) => lifeMoves(r, i + 1)),
    );
    const isBase = label === "baseline";
    return {
      condition: label,
      config,
      n_runs: runIds.length,
      per_life,
      avg_score: Math.round(all.reduce((a, b) => a + b, 0) / all.length),
      avg_moves: Math.round(allMoves.reduce((a, b) => a + b, 0) / allMoves.length),
      repeated_actions: isBase ? 214 : 31,
      guardrail_blocks: isBase ? 0 : 68,
    };
  };

  const full: HarnessConfig = { reflection: true, rules: true, memory: true, guardrails: true };
  const none: HarnessConfig = { reflection: false, rules: false, memory: false, guardrails: false };

  return {
    conditions: [
      build("baseline", ["baseline-1", "baseline-2", "baseline-3"], none),
      build("harness", ["harness-1", "harness-2", "harness-3"], full),
      ...ABLATIONS.map((a) => build(a.label, [a.id], a.config)),
    ],
  };
}

export function ablationList() {
  return ABLATIONS;
}

export function deathCauseBreakdown() {
  return Array.from({ length: 10 }, (_, i) => {
    const life = i + 1;
    const lives = ["harness-1", "harness-2", "harness-3"].map((r) => getLives(r)[i]!);
    const count = (needle: string) => lives.filter((l) => l.death_cause.includes(needle)).length;
    return {
      life,
      grue: count("grue"),
      troll: count("troll"),
      fall: count("fell"),
      water: count("drowned"),
      survived: count("survived"),
    };
  });
}
