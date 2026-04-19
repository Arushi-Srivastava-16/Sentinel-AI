/**
 * Zustand store for live WebSocket events.
 * Keeps the last 500 events in memory and computes derived metrics.
 */

import { create } from "zustand";
import type { LiveEvent, MetricsSummary } from "@/types";

const MAX_EVENTS = 500;

interface EventStore {
  events:       LiveEvent[];
  isConnected:  boolean;
  circuitStates: Record<string, string>;  // service → state

  addEvent:       (event: LiveEvent) => void;
  setConnected:   (v: boolean) => void;
  setCircuitState:(service: string, state: string) => void;
  clearEvents:    () => void;
  getMetrics:     () => MetricsSummary;
}

export const useEventStore = create<EventStore>((set, get) => ({
  events:        [],
  isConnected:   false,
  circuitStates: {},

  addEvent: (event) =>
    set((s) => ({
      events: [event, ...s.events].slice(0, MAX_EVENTS),
    })),

  setConnected: (v) => set({ isConnected: v }),

  setCircuitState: (service, state) =>
    set((s) => ({
      circuitStates: { ...s.circuitStates, [service]: state },
    })),

  clearEvents: () => set({ events: [] }),

  getMetrics: (): MetricsSummary => {
    const { events } = get();
    if (events.length === 0) {
      return {
        total: 0, allowed: 0, blocked: 0, humanReview: 0,
        avgLatencyMs: 0, fastPathCount: 0, cognitivePathCount: 0,
        rateLimitHits: 0,
      };
    }

    const allowed        = events.filter((e) => e.verdict === "ALLOWED").length;
    const blocked        = events.filter((e) => e.verdict === "BLOCKED").length;
    const humanReview    = events.filter((e) => e.verdict === "HUMAN_REVIEW").length;
    const fastPathCount  = events.filter((e) => e.path === "fast_path").length;
    const cogCount       = events.filter((e) => e.path === "cognitive_path").length;
    const avgLatencyMs   = events.reduce((s, e) => s + e.latency_ms, 0) / events.length;

    return {
      total:             events.length,
      allowed,
      blocked,
      humanReview,
      avgLatencyMs:      Math.round(avgLatencyMs * 10) / 10,
      fastPathCount,
      cognitivePathCount: cogCount,
      rateLimitHits:     0,
    };
  },
}));
