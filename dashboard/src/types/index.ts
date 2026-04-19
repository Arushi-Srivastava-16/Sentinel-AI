import { z } from "zod";

// =============================================================================
// Enums
// =============================================================================

export type Verdict = "ALLOWED" | "BLOCKED" | "HUMAN_REVIEW";
export type DecisionPath = "fast_path" | "cognitive_path";
export type ServiceStatus = "ok" | "down" | "circuit_open";

// =============================================================================
// WebSocket event schemas (validated with zod at runtime)
// =============================================================================

export const ToolCallDecisionEventSchema = z.object({
  event_type: z.literal("tool_call_decision"),
  timestamp: z.string(),
  decision_id: z.string(),
  agent_id: z.string().optional(),
  tool_name: z.string().optional(),
  verdict: z.enum(["ALLOWED", "BLOCKED", "HUMAN_REVIEW"]),
  path: z.enum(["fast_path", "cognitive_path"]).optional(),
  latency_ms: z.number().optional(),
  policy_version: z.string().optional(),
  reason: z.string().optional(),
  confidence: z.number().optional(),
});

export const CircuitBreakerEventSchema = z.object({
  event_type: z.literal("circuit_breaker_state_change"),
  service: z.string(),
  state: z.enum(["closed", "open", "half_open"]),
  timestamp: z.string(),
});

export const RateLimitHitEventSchema = z.object({
  event_type: z.literal("rate_limit_hit"),
  agent_id: z.string(),
  timestamp: z.string(),
});

export const PolicyActivatedEventSchema = z.object({
  event_type: z.literal("policy_activated"),
  policy_group: z.string(),
  version: z.string(),
  timestamp: z.string(),
});

export const PingEventSchema = z.object({
  type: z.literal("ping"),
  timestamp: z.string(),
});

export const ConnectedEventSchema = z.object({
  event_type: z.literal("connected"),
  session_id: z.string(),
  timestamp: z.string(),
});

export const WsEventSchema = z.discriminatedUnion("event_type", [
  ToolCallDecisionEventSchema,
  CircuitBreakerEventSchema,
  RateLimitHitEventSchema,
  PolicyActivatedEventSchema,
  ConnectedEventSchema,
]);

export type ToolCallDecisionEvent = z.infer<typeof ToolCallDecisionEventSchema>;
export type CircuitBreakerEvent = z.infer<typeof CircuitBreakerEventSchema>;
export type PolicyActivatedEvent = z.infer<typeof PolicyActivatedEventSchema>;
export type WsEvent = z.infer<typeof WsEventSchema>;

// =============================================================================
// API response types
// =============================================================================

export interface RateLimitInfo {
  tokens_remaining: number;
  tokens_max: number;
  reset_at: string;
}

export interface DecisionResponse {
  decision_id: string;
  verdict: Verdict;
  reason: string;
  path: DecisionPath;
  latency_ms: number;
  policy_version: string;
  confidence?: number;
  rate_limit?: RateLimitInfo;
}

export interface AgentInfo {
  agent_id: string;
  name: string;
  policy_group: string;
  tenant_id: string;
  registered_at: string;
  total_requests: number;
  last_active?: string;
}

export interface PolicyVersionInfo {
  id: string;
  policy_group: string;
  version: string;
  description: string;
  effective_from: string;
  effective_until?: string;
  parent_version?: string;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  services: {
    redis: ServiceStatus;
    neo4j: ServiceStatus;
    ollama: ServiceStatus;
  };
}

// =============================================================================
// Dashboard-local state types
// =============================================================================

export interface LiveEvent {
  id: string;
  timestamp: string;
  verdict: Verdict;
  tool_name: string;
  agent_id: string;
  latency_ms: number;
  path: DecisionPath;
  reason: string;
  policy_version: string;
}

export interface MetricsSummary {
  total: number;
  allowed: number;
  blocked: number;
  humanReview: number;
  avgLatencyMs: number;
  fastPathCount: number;
  cognitivePathCount: number;
  rateLimitHits: number;
}

// Neo4j graph viz nodes/links
export interface GraphNode {
  id: string;
  label: string;
  type: "agent" | "session" | "tool_call" | "decision" | "policy";
  verdict?: Verdict;
}

export interface GraphLink {
  source: string;
  target: string;
  label: string;
}
