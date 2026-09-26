import { useEffect, useRef, useState } from "react";
import type { Move } from "@/api/types";

function useTypewriter(text: string, speedMs = 12) {
  const [shown, setShown] = useState("");
  useEffect(() => {
    setShown("");
    let i = 0;
    const id = setInterval(() => {
      i += 2;
      setShown(text.slice(0, i));
      if (i >= text.length) clearInterval(id);
    }, speedMs);
    return () => clearInterval(id);
  }, [text, speedMs]);
  return shown;
}

export function Terminal({
  moves,
  index,
  life,
  className = "",
}: {
  moves: Move[];
  index: number;
  life: number;
  className?: string;
}) {
  const current = moves[index];
  const history = moves.slice(Math.max(0, index - 14), index);
  const typed = useTypewriter(current?.observation ?? "");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [index]);

  return (
    <div className={`panel crt flex flex-col overflow-hidden ${className}`}>
      <div className="relative z-10 flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <span className="panel-title">The Game</span>
        <div className="flex gap-4 font-mono text-xs text-muted-foreground">
          <span>
            LIFE <span className="text-foreground">{life}</span>
          </span>
          <span>
            MOVE <span className="text-foreground">{current?.move ?? 0}</span>
          </span>
          <span>
            SCORE <span className="phosphor-text">{current?.score ?? 0}</span>
          </span>
        </div>
      </div>

      <div className="relative z-10 flex-1 space-y-3 overflow-y-auto p-4 font-mono text-sm leading-relaxed">
        {history.map((m) => (
          <div key={m.move} className="space-y-1 opacity-70">
            <p className="phosphor-text">{m.observation}</p>
            <p className="text-foreground">
              <span className="text-muted-foreground">&gt; </span>
              {m.command}
            </p>
          </div>
        ))}
        {current && (
          <div className="space-y-1">
            <p className="phosphor-text">{typed}</p>
            <p className="text-foreground">
              <span className="text-muted-foreground">&gt; </span>
              {current.command}
              <span className="caret phosphor-text">▊</span>
            </p>
            {current.repeated_action && (
              <p className="font-mono text-xs text-baseline">↻ Repeated action</p>
            )}
          </div>
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
