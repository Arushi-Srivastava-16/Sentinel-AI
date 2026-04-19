/**
 * VerdictChart — donut chart showing verdict distribution.
 * Updates live as WebSocket events arrive.
 */

import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { useEventStore } from "@/store/eventStore";

const COLOURS: Record<string, string> = {
  Allowed:      "#22c55e",
  Blocked:      "#ef4444",
  "Human Review": "#eab308",
};

export function VerdictChart() {
  const getMetrics = useEventStore((s) => s.getMetrics);
  const m = getMetrics();

  const data = [
    { name: "Allowed",       value: m.allowed },
    { name: "Blocked",       value: m.blocked },
    { name: "Human Review",  value: m.humanReview },
  ].filter((d) => d.value > 0);

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-sentinel-muted text-sm font-mono">
        No data yet
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-sentinel-border bg-sentinel-surface p-4">
      <h2 className="text-sm font-mono font-semibold text-white mb-3">Verdict Distribution</h2>
      <ResponsiveContainer width="100%" height={200}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={55}
            outerRadius={80}
            paddingAngle={3}
            dataKey="value"
          >
            {data.map((entry) => (
              <Cell key={entry.name} fill={COLOURS[entry.name] ?? "#6b7280"} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              backgroundColor: "#1a1d27",
              border: "1px solid #2a2d3a",
              borderRadius: "8px",
              fontFamily: "JetBrains Mono, monospace",
              fontSize: "12px",
              color: "#fff",
            }}
          />
          <Legend
            formatter={(value) => (
              <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "11px", color: "#9ca3af" }}>
                {value}
              </span>
            )}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
