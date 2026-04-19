/**
 * useWebSocket — connects to /ws/dashboard and pushes events into the
 * Zustand event store. Handles reconnection automatically.
 */

import { useEffect, useRef } from "react";
import { useEventStore } from "@/store/eventStore";
import { WsEventSchema } from "@/types";

const WS_URL =
  import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/ws/dashboard";
const API_KEY =
  import.meta.env.VITE_ADMIN_API_KEY || "snl_admin_dev_changeme_replace_me";
const RECONNECT_DELAY_MS = 3000;

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { addEvent, setConnected, setCircuitState } = useEventStore();

  function connect() {
    const url = `${WS_URL}?token=${encodeURIComponent(API_KEY)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
    };

    ws.onmessage = (evt) => {
      try {
        const raw = JSON.parse(evt.data as string);

        // Handle ping
        if (raw.type === "ping") {
          ws.send(JSON.stringify({ type: "pong" }));
          return;
        }

        const parsed = WsEventSchema.safeParse(raw);
        if (!parsed.success) return;

        const event = parsed.data;
        switch (event.event_type) {
          case "tool_call_decision":
            addEvent({
              id:             event.decision_id,
              timestamp:      event.timestamp,
              verdict:        event.verdict,
              tool_name:      event.tool_name ?? "unknown",
              agent_id:       event.agent_id ?? "unknown",
              latency_ms:     event.latency_ms ?? 0,
              path:           event.path ?? "fast_path",
              reason:         event.reason ?? "",
              policy_version: event.policy_version ?? "",
            });
            break;

          case "circuit_breaker_state_change":
            setCircuitState(event.service, event.state);
            break;

          case "policy_activated":
          case "rate_limit_hit":
          case "connected":
            // Future: surface in a notifications panel
            break;
        }
      } catch {
        // Ignore parse errors
      }
    };

    ws.onclose = () => {
      setConnected(false);
      reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY_MS);
    };

    ws.onerror = () => {
      ws.close();
    };
  }

  useEffect(() => {
    connect();
    return () => {
      if (wsRef.current) wsRef.current.close();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    };
  }, []);
}
