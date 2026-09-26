import { createFileRoute } from "@tanstack/react-router";
import {
  Database,
  Eye,
  GitBranch,
  Link2,
  Lock,
  MonitorPlay,
  Play,
  Quote,
  ScanSearch,
  Share2,
  Shield,
  ShieldCheck,
  Terminal,
  Waves,
} from "lucide-react";
import type { ComponentType } from "react";

export const Route = createFileRoute("/about")({
  head: () => ({
    meta: [
      { title: "GRUE LAB" },
      {
        name: "description",
        content: "What we built, how Atlas is used, and why this harness is different.",
      },
      { property: "og:title", content: "About · GRUE LAB" },
      {
        property: "og:description",
        content: "What we built, how Atlas is used, and why this harness is different.",
      },
    ],
  }),
  component: About,
});

type BulletItem = { icon: ComponentType<{ className?: string }>; text: string };

function FlowSteps({ steps }: { steps: string[] }) {
  return (
    <div>
      {steps.map((step, i) => (
        <div key={step}>
          <div className="flex items-center gap-2.5">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full border-2 border-lesson bg-background" />
            <span className="rounded-full border border-lesson/30 px-3 py-1 font-mono text-xs text-foreground">
              {step}
            </span>
          </div>
          {i < steps.length - 1 && <div className="ml-[4px] h-4 w-px bg-lesson/25" />}
        </div>
      ))}
    </div>
  );
}

function Bullets({ items }: { items: BulletItem[] }) {
  return (
    <ul className="space-y-4">
      {items.map(({ icon: Icon, text }) => (
        <li key={text} className="flex items-start gap-3 text-sm text-foreground/80">
          <Icon className="mt-0.5 h-4 w-4 shrink-0 text-lesson" />
          <span>{text}</span>
        </li>
      ))}
    </ul>
  );
}

function Section({
  icon: Icon,
  title,
  steps,
  bullets,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  steps: string[];
  bullets: BulletItem[];
}) {
  return (
    <section className="panel flex flex-col gap-6 p-8">
      <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-lesson/25 bg-lesson/5">
        <Icon className="h-5 w-5 text-lesson" />
      </div>
      <h2 className="text-lg font-semibold text-foreground">{title}</h2>
      <FlowSteps steps={steps} />
      <Bullets items={bullets} />
    </section>
  );
}

function About() {
  return (
    <div className="space-y-10 py-4">
      <div>
        <h1 className="text-4xl font-bold tracking-tight text-foreground">About</h1>
        <p className="mt-2 text-muted-foreground">A self-improving game-playing AI harness.</p>
      </div>

      <div className="grid gap-6 md:grid-cols-3">
        <Section
          icon={Play}
          title="What we built"
          steps={["G1", "G2", "G3", "…", "G10"]}
          bullets={[
            { icon: Terminal, text: "Plays Zork I with a self-improving harness" },
            { icon: GitBranch, text: "Same model, same prompt: baseline vs harness" },
            { icon: Link2, text: "Ten games per chain, each builds on the last" },
            { icon: MonitorPlay, text: "Live Run replays both side by side" },
          ]}
        />
        <Section
          icon={Database}
          title="How we use Atlas"
          steps={["Agent plays", "Atlas stores it", "Map + memory recall"]}
          bullets={[
            { icon: Database, text: "Every run, move, and rule stored in Atlas" },
            { icon: Waves, text: "Map builds live from an Atlas change stream" },
            { icon: ScanSearch, text: "Memory recall via Atlas Vector Search" },
            { icon: Share2, text: "Automated Embedding, no separate pipeline" },
          ]}
        />
        <Section
          icon={ShieldCheck}
          title="Why it's different"
          steps={["Soft rule", "Replay every game", "Hard rule"]}
          bullets={[
            { icon: Shield, text: "Rules earn authority by replay, not by trust" },
            { icon: Quote, text: "Every belief cites the move that proved it" },
            { icon: Eye, text: "Only sees what a human player sees" },
            { icon: Lock, text: "Same model, prompt, and seed throughout" },
          ]}
        />
      </div>
    </div>
  );
}
