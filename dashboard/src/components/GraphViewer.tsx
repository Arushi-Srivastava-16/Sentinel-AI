/**
 * GraphViewer — force-directed graph of the audit trail.
 * Nodes: agents, sessions, tool calls, decisions.
 * Colour-coded by verdict (green/red/yellow).
 *
 * Uses react-force-graph-2d for WebGL rendering.
 */

import { useCallback, useRef, useMemo } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { useEventStore } from "@/store/eventStore";
import type { GraphNode, GraphLink } from "@/types";

function buildGraph(events: ReturnType<typeof useEventStore.getState>["events"]) {
  const nodes: GraphNode[] = [];
  const links: GraphLink[] = [];
  const seen = new Set<string>();

  const addNode = (node: GraphNode) => {
    if (!seen.has(node.id)) {
      seen.add(node.id);
      nodes.push(node);
    }
  };

  for (const e of events.slice(0, 100)) {   // cap at 100 for perf
    const agentId   = `agent:${e.agent_id}`;
    const decId     = `dec:${e.id}`;
    const toolId    = `tool:${e.id}`;

    addNode({ id: agentId, label: e.agent_id, type: "agent" });
    addNode({ id: toolId,  label: e.tool_name, type: "tool_call" });
    addNode({ id: decId,   label: e.verdict, type: "decision", verdict: e.verdict });

    links.push({ source: agentId, target: toolId, label: "called" });
    links.push({ source: toolId,  target: decId,  label: "resulted_in" });
  }

  return { nodes, links };
}

const NODE_COLOURS: Record<string, string> = {
  agent:      "#3b82f6",
  session:    "#8b5cf6",
  tool_call:  "#6b7280",
  decision:   "#374151",
  policy:     "#f59e0b",
};

const VERDICT_COLOURS: Record<string, string> = {
  ALLOWED:      "#22c55e",
  BLOCKED:      "#ef4444",
  HUMAN_REVIEW: "#eab308",
};

export function GraphViewer() {
  const events = useEventStore((s) => s.events);
  const graphRef = useRef<any>(null);

  const { nodes, links } = useMemo(() => buildGraph(events), [events]);

  const nodeColor = useCallback((node: GraphNode) => {
    if (node.type === "decision" && node.verdict) {
      return VERDICT_COLOURS[node.verdict] ?? NODE_COLOURS.decision;
    }
    return NODE_COLOURS[node.type] ?? "#6b7280";
  }, []);

  const nodeLabel = useCallback((node: GraphNode) => node.label, []);

  if (nodes.length === 0) {
    return (
      <div className="rounded-xl border border-sentinel-border bg-sentinel-surface p-4 flex items-center justify-center h-80">
        <p className="text-sentinel-muted text-sm font-mono">Audit graph will appear here…</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-sentinel-border bg-sentinel-surface overflow-hidden">
      <div className="px-4 py-3 border-b border-sentinel-border flex items-center justify-between">
        <h2 className="text-sm font-mono font-semibold text-white">
          Audit Trail Graph
          <span className="ml-2 text-xs text-sentinel-muted">({nodes.length} nodes)</span>
        </h2>
        <div className="flex gap-3 text-[11px] font-mono text-sentinel-muted">
          <span><span style={{ color: "#22c55e" }}>●</span> Allowed</span>
          <span><span style={{ color: "#ef4444" }}>●</span> Blocked</span>
          <span><span style={{ color: "#eab308" }}>●</span> Review</span>
          <span><span style={{ color: "#3b82f6" }}>●</span> Agent</span>
        </div>
      </div>
      <ForceGraph2D
        ref={graphRef}
        graphData={{ nodes, links }}
        nodeLabel={nodeLabel}
        nodeColor={nodeColor}
        nodeRelSize={5}
        linkColor={() => "#2a2d3a"}
        linkDirectionalArrowLength={4}
        linkDirectionalArrowRelPos={1}
        backgroundColor="#1a1d27"
        width={undefined}
        height={320}
        nodeCanvasObject={(node: any, ctx, globalScale) => {
          const label = node.label as string;
          const fontSize = Math.max(10 / globalScale, 3);
          ctx.font = `${fontSize}px JetBrains Mono, monospace`;
          ctx.fillStyle = nodeColor(node);
          ctx.beginPath();
          ctx.arc(node.x, node.y, 5, 0, 2 * Math.PI);
          ctx.fill();
          if (globalScale > 1) {
            ctx.fillStyle = "#9ca3af";
            ctx.fillText(label.slice(0, 16), node.x + 6, node.y + 2);
          }
        }}
      />
    </div>
  );
}
