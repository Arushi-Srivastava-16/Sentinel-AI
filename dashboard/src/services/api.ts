/**
 * HTTP API client — wraps fetch with auth headers and error handling.
 * All endpoints proxied through Vite dev server to http://localhost:8000
 */

const API_KEY =
  import.meta.env.VITE_ADMIN_API_KEY || "snl_admin_dev_changeme_replace_me";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Sentinel-Agent-Key": API_KEY,
      ...options.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.detail?.message ?? `HTTP ${res.status}`);
  }

  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------
export async function fetchHealth() {
  return request<import("@/types").HealthResponse>("/health");
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------
export async function fetchAgent(agentId: string) {
  return request<import("@/types").AgentInfo>(`/v1/agents/${agentId}`);
}

// ---------------------------------------------------------------------------
// Policies
// ---------------------------------------------------------------------------
export async function fetchPolicies() {
  return request<import("@/types").PolicyVersionInfo[]>("/v1/policies");
}

export async function activatePolicy(policyGroup: string, version: string) {
  return request<import("@/types").PolicyVersionInfo>(
    `/v1/policies/${policyGroup}/activate`,
    {
      method: "POST",
      body: JSON.stringify({ version }),
    }
  );
}
