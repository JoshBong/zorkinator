export type Condition = "baseline" | "harness" | "ablation";

export interface HarnessConfig {
  reflection: boolean;
  rules: boolean;
  memory: boolean;
  guardrails: boolean;
}

export interface Run {
  run_id: string;
  condition: Condition;
  model: string;
  config: HarnessConfig;
  status: "running" | "done";
  lives_count: number;
  created_at: string;
}

export interface Life {
  life: number;
  score: number;
  moves: number;
  death_cause: string;
  rules_learned: string[];
}

export interface RetrievedRule {
  rule_id: string;
  version: number;
  text: string;
  similarity: number;
}

export interface Guardrail {
  blocked: boolean;
  rule_id: string;
  original_command: string;
  replacement_command: string;
}

export interface Move {
  move: number;
  observation: string;
  command: string;
  room: string;
  score: number;
  retrieved_rules: RetrievedRule[];
  guardrail: Guardrail | null;
  repeated_action: boolean;
}

export interface Reflection {
  cause: string;
  effect: string;
  lesson_text: string;
  rule_id: string;
}

export type RuleType = "rule" | "guardrail" | "memory";
export type RuleStatus = "active" | "superseded" | "rolled_back";

export interface Rule {
  rule_id: string;
  version: number;
  type: RuleType;
  text: string;
  status: RuleStatus;
  learned_from: { run_id: string; life: number; move: number };
  score_impact: number;
  parent_version: number | null;
  created_at: string;
}

export interface MapRoom {
  name: string;
  dark: boolean;
  first_seen_life: number;
  death_count: number;
}

export interface MapEdge {
  from: string;
  to: string;
  direction: string;
}

export interface RunMap {
  rooms: MapRoom[];
  edges: MapEdge[];
}

export interface PerLifePoint {
  life: number;
  mean: number;
  min: number;
  max: number;
}

export interface EvalCondition {
  condition: string;
  config: HarnessConfig;
  n_runs: number;
  per_life: PerLifePoint[];
  avg_score: number;
  avg_moves: number;
  repeated_actions: number;
  guardrail_blocks: number;
}

export interface EvalSummary {
  conditions: EvalCondition[];
}

export type StreamEvent =
  | { type: "move"; data: Move }
  | { type: "death"; data: { life: number; move: number; cause: string } }
  | { type: "rule_created"; data: Rule }
  | { type: "guardrail_block"; data: Guardrail & { move: number } };
