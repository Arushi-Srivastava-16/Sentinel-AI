/**
 * StatusBar — top of the page. Shows connection status + circuit breaker state.
 */

import { useEventStore } from "@/store/eventStore";
import { cn } from "@/utils/cn";

export function StatusBar() {
  const isConnected    = useEventStore((s) => s.isConnected);
  const circuitStates  = useEventStore((s) => s.circuitStates);
  const ollamaState    = circuitStates["ollama"] ?? "closed";

  const circuitColour =
    ollamaState === "closed"     ? "text-sentinel-green" :
    ollamaState === "half_open"  ? "text-sentinel-yellow" :
                                   "text-sentinel-red";

  return (
    <header className="flex items-center justify-between px-6 py-3 border-b border-sentinel-border bg-sentinel-surface/50 backdrop-blur-sm sticky top-0 z-10">
      {/* Logo */}
      <div className="flex items-center gap-2">
        <span className="text-sentinel-blue font-mono font-bold text-lg tracking-tight">
          ⬡ SENTINEL
        </span>
        <span className="text-[10px] text-sentinel-muted font-mono uppercase tracking-widest">
          AI Governance
        </span>
      </div>

      {/* Status indicators */}
      <div className="flex items-center gap-4 text-xs font-mono">
        {/* WebSocket */}
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              isConnected ? "bg-sentinel-green animate-pulse-slow" : "bg-sentinel-red"
            )}
          />
          <span className="text-sentinel-muted">
            {isConnected ? "Live" : "Reconnecting…"}
          </span>
        </div>

        {/* Ollama circuit */}
        <div className="flex items-center gap-1.5">
          <span className={cn("font-semibold", circuitColour)}>
            ⚡ Ollama
          </span>
          <span className="text-sentinel-muted capitalize">{ollamaState.replace("_", " ")}</span>
        </div>
      </div>
    </header>
  );
}
