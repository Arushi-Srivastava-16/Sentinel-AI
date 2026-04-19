/**
 * EventFeed — real-time scrolling list of tool call decisions.
 * New events animate in from the top.
 */

import { formatDistanceToNow } from "date-fns";
import { useEventStore } from "@/store/eventStore";
import type { LiveEvent, Verdict } from "@/types";
import { cn } from "@/utils/cn";

function verdictBadge(v: Verdict) {
  const cfg = {
    ALLOWED:      { bg: "bg-sentinel-green/10",  text: "text-sentinel-green",  label: "ALLOWED" },
    BLOCKED:      { bg: "bg-sentinel-red/10",    text: "text-sentinel-red",    label: "BLOCKED" },
    HUMAN_REVIEW: { bg: "bg-sentinel-yellow/10", text: "text-sentinel-yellow", label: "REVIEW" },
  }[v];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-mono font-semibold",
        cfg.bg, cfg.text
      )}
    >
      {cfg.label}
    </span>
  );
}

function pathBadge(path: string) {
  return path === "fast_path" ? (
    <span className="text-[10px] font-mono text-sentinel-blue/70">⚡ fast</span>
  ) : (
    <span className="text-[10px] font-mono text-sentinel-purple/70">🧠 cognitive</span>
  );
}

function EventRow({ event }: { event: LiveEvent }) {
  const ago = formatDistanceToNow(new Date(event.timestamp), { addSuffix: true });
  return (
    <div className="flex items-start gap-3 rounded-lg px-3 py-2 hover:bg-sentinel-border/30 animate-fade-in transition-colors">
      <div className="w-[90px] shrink-0">{verdictBadge(event.verdict)}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-white truncate">{event.tool_name}</span>
          {pathBadge(event.path)}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-xs text-sentinel-muted font-mono truncate">{event.agent_id}</span>
          <span className="text-xs text-sentinel-muted">·</span>
          <span className="text-xs text-sentinel-muted font-mono">{event.latency_ms.toFixed(1)}ms</span>
        </div>
        {event.reason && (
          <p className="text-[11px] text-sentinel-muted/80 mt-0.5 truncate">{event.reason}</p>
        )}
      </div>
      <span className="shrink-0 text-[10px] text-sentinel-muted font-mono whitespace-nowrap">
        {ago}
      </span>
    </div>
  );
}

export function EventFeed() {
  const events = useEventStore((s) => s.events);
  const clearEvents = useEventStore((s) => s.clearEvents);

  return (
    <div className="flex flex-col h-full rounded-xl border border-sentinel-border bg-sentinel-surface overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-sentinel-border">
        <h2 className="text-sm font-mono font-semibold text-white">
          Live Event Feed
          <span className="ml-2 text-xs text-sentinel-muted">({events.length})</span>
        </h2>
        <button
          onClick={clearEvents}
          className="text-xs text-sentinel-muted hover:text-white font-mono transition-colors"
        >
          clear
        </button>
      </div>

      {/* Events */}
      <div className="flex-1 overflow-y-auto">
        {events.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-sentinel-muted text-sm font-mono">
            Waiting for decisions…
          </div>
        ) : (
          <div className="divide-y divide-sentinel-border/30">
            {events.map((e) => (
              <EventRow key={e.id} event={e} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
