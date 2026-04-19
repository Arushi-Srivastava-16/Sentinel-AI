/**
 * MetricsCards — top row KPI tiles.
 * Reads from Zustand event store (live, no HTTP fetch needed).
 */

import { useEventStore } from "@/store/eventStore";
import { cn } from "@/utils/cn";

interface CardProps {
  label: string;
  value: string | number;
  sub?: string;
  colour?: "green" | "red" | "yellow" | "blue" | "default";
}

function Card({ label, value, sub, colour = "default" }: CardProps) {
  const border = {
    green:   "border-sentinel-green/40",
    red:     "border-sentinel-red/40",
    yellow:  "border-sentinel-yellow/40",
    blue:    "border-sentinel-blue/40",
    default: "border-sentinel-border",
  }[colour];

  const textColour = {
    green:   "text-sentinel-green",
    red:     "text-sentinel-red",
    yellow:  "text-sentinel-yellow",
    blue:    "text-sentinel-blue",
    default: "text-white",
  }[colour];

  return (
    <div
      className={cn(
        "rounded-xl border bg-sentinel-surface p-4 flex flex-col gap-1",
        border
      )}
    >
      <span className="text-xs font-mono text-sentinel-muted uppercase tracking-widest">
        {label}
      </span>
      <span className={cn("text-3xl font-mono font-semibold", textColour)}>
        {value}
      </span>
      {sub && (
        <span className="text-xs text-sentinel-muted font-mono">{sub}</span>
      )}
    </div>
  );
}

export function MetricsCards() {
  const getMetrics = useEventStore((s) => s.getMetrics);
  const isConnected = useEventStore((s) => s.isConnected);
  const m = getMetrics();

  const approvalRate =
    m.total > 0 ? Math.round((m.allowed / m.total) * 100) : 0;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Card
        label="Total Decisions"
        value={m.total.toLocaleString()}
        sub={isConnected ? "● live" : "○ disconnected"}
        colour={isConnected ? "blue" : "default"}
      />
      <Card
        label="Allowed"
        value={m.allowed.toLocaleString()}
        sub={`${approvalRate}% approval rate`}
        colour="green"
      />
      <Card
        label="Blocked"
        value={m.blocked.toLocaleString()}
        sub={`${m.humanReview} pending review`}
        colour="red"
      />
      <Card
        label="Avg Latency"
        value={`${m.avgLatencyMs}ms`}
        sub={`fast: ${m.fastPathCount} · cognitive: ${m.cognitivePathCount}`}
        colour="blue"
      />
    </div>
  );
}
