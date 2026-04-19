/**
 * PolicyPanel — list policy versions, activate a version live.
 * Demo C relies on the "Activate" button here.
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchPolicies, activatePolicy } from "@/services/api";
import { cn } from "@/utils/cn";

export function PolicyPanel() {
  const qc = useQueryClient();
  const [activating, setActivating] = useState<string | null>(null);

  const { data: policies = [], isLoading } = useQuery({
    queryKey: ["policies"],
    queryFn: fetchPolicies,
    refetchInterval: 5000,
  });

  const activate = useMutation({
    mutationFn: ({ group, version }: { group: string; version: string }) =>
      activatePolicy(group, version),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["policies"] });
      setActivating(null);
    },
    onError: (err: Error) => {
      alert(`Failed to activate: ${err.message}`);
      setActivating(null);
    },
  });

  if (isLoading) {
    return (
      <div className="rounded-xl border border-sentinel-border bg-sentinel-surface p-4">
        <p className="text-sentinel-muted text-sm font-mono">Loading policies…</p>
      </div>
    );
  }

  // Group by policy_group
  const groups: Record<string, typeof policies> = {};
  for (const p of policies) {
    if (!groups[p.policy_group]) groups[p.policy_group] = [];
    groups[p.policy_group].push(p);
  }

  return (
    <div className="rounded-xl border border-sentinel-border bg-sentinel-surface overflow-hidden">
      <div className="px-4 py-3 border-b border-sentinel-border">
        <h2 className="text-sm font-mono font-semibold text-white">Policy Versions</h2>
        <p className="text-xs text-sentinel-muted font-mono mt-0.5">
          Activate a version to change enforcement live (Demo C)
        </p>
      </div>

      <div className="divide-y divide-sentinel-border/50">
        {Object.entries(groups).map(([group, versions]) => (
          <div key={group} className="p-4">
            <h3 className="text-xs font-mono text-sentinel-muted uppercase tracking-wider mb-2">
              {group}
            </h3>
            <div className="space-y-2">
              {versions.map((v) => {
                const key = `${v.policy_group}-${v.version}`;
                const isActive = activating === key;
                return (
                  <div
                    key={key}
                    className="flex items-center justify-between rounded-lg bg-sentinel-bg px-3 py-2"
                  >
                    <div>
                      <span className="text-sm font-mono text-white">v{v.version}</span>
                      {v.parent_version && (
                        <span className="ml-2 text-[10px] text-sentinel-muted font-mono">
                          ← v{v.parent_version}
                        </span>
                      )}
                      <p className="text-[11px] text-sentinel-muted font-mono mt-0.5 max-w-xs truncate">
                        {v.description}
                      </p>
                    </div>
                    <button
                      disabled={activate.isPending && isActive}
                      onClick={() => {
                        setActivating(key);
                        activate.mutate({ group: v.policy_group, version: v.version });
                      }}
                      className={cn(
                        "ml-4 shrink-0 rounded px-3 py-1 text-xs font-mono font-semibold transition-colors",
                        "bg-sentinel-blue/10 text-sentinel-blue hover:bg-sentinel-blue/20",
                        "disabled:opacity-50 disabled:cursor-not-allowed"
                      )}
                    >
                      {isActive && activate.isPending ? "Activating…" : "Activate"}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        ))}

        {policies.length === 0 && (
          <div className="p-4 text-sentinel-muted text-sm font-mono">
            No policies loaded.
          </div>
        )}
      </div>
    </div>
  );
}
