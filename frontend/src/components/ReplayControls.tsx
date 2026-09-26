import { Pause, Play, RotateCcw } from "lucide-react";
import { SPEEDS, type Speed } from "@/lib/use-replay";
import type { Move } from "@/api/types";

export function ReplayControls({
  moves,
  index,
  setIndex,
  playing,
  setPlaying,
  speed,
  setSpeed,
  restart,
  live,
}: {
  moves: Move[];
  index: number;
  setIndex: (i: number) => void;
  playing: boolean;
  setPlaying: (p: boolean) => void;
  speed: Speed;
  setSpeed: (s: Speed) => void;
  restart: () => void;
  live: boolean;
}) {
  const total = Math.max(1, moves.length - 1);

  return (
    <div className="panel flex flex-wrap items-center gap-4 px-4 py-3">
      <span
        className={`rounded-full px-2.5 py-1 font-mono text-[10px] tracking-widest uppercase ${
          live ? "bg-harness/15 text-harness" : "bg-lesson/15 text-lesson"
        }`}
      >
        {live ? "● Live" : "▮ Replay"}
      </span>

      <button
        onClick={() => setPlaying(!playing)}
        className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-foreground hover:bg-secondary"
        aria-label={playing ? "Pause" : "Play"}
      >
        {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
      </button>
      <button
        onClick={restart}
        className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-foreground hover:bg-secondary"
        aria-label="Restart"
      >
        <RotateCcw className="h-4 w-4" />
      </button>

      <div className="flex gap-1">
        {SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => setSpeed(s)}
            className={`rounded-md px-2 py-1 font-mono text-xs ${
              speed === s
                ? "bg-harness/20 text-harness"
                : "text-muted-foreground hover:bg-secondary"
            }`}
          >
            {s}x
          </button>
        ))}
      </div>

      <div className="relative min-w-[220px] flex-1">
        <input
          type="range"
          min={0}
          max={total}
          value={index}
          onChange={(e) => setIndex(Number(e.target.value))}
          className="w-full accent-[var(--harness)]"
          aria-label="Timeline"
        />
        <div className="pointer-events-none absolute inset-x-0 top-0 h-2">
          {moves.map((m, i) =>
            m.repeated_action ? (
              <span
                key={m.move}
                style={{ left: `${(i / total) * 100}%` }}
                className="absolute top-0 h-2 w-[2px] bg-lesson"
              />
            ) : null,
          )}
        </div>
      </div>

      <span className="font-mono text-xs text-muted-foreground">
        {index + 1}/{moves.length}
      </span>
    </div>
  );
}
