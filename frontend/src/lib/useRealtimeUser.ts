"use client";

import { useEffect, useRef, useState } from "react";
import { API_URL } from "./api";

export type RealtimeEvent =
  | { type: "hello"; user_id: string }
  | { type: "ping" }
  | { type: "hosted_agent_status"; hosted_id: string; agent_id: string | null; status: string }
  | { type: "owner_message"; hosted_id: string; role: string; content: string }
  | { type: "notification"; text: string }
  | { type: string; [k: string]: unknown };

type Listener = (e: RealtimeEvent) => void;

/**
 * Connects to the platform user WebSocket and dispatches every event to the
 * provided handler. Auto-reconnects with exponential backoff (1s → 30s).
 *
 * Returns the latest received event for components that prefer state over
 * callbacks (e.g. status badges).
 */
export function useRealtimeUser(onEvent?: Listener) {
  const [last, setLast] = useState<RealtimeEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const onEventRef = useRef<Listener | undefined>(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    let cancelled = false;
    let retry = 1000;

    const connect = () => {
      if (cancelled) return;
      const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
      if (!token) {
        setTimeout(connect, 2000);
        return;
      }
      const wsBase = API_URL.replace(/^http/, "ws");
      const ws = new WebSocket(`${wsBase}/api/v1/users/ws?token=${encodeURIComponent(token)}`);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        retry = 1000;
      };
      ws.onmessage = (msg) => {
        try {
          const ev = JSON.parse(msg.data) as RealtimeEvent;
          if (ev.type === "ping") {
            try { ws.send(JSON.stringify({ type: "pong" })); } catch { /* noop */ }
            return;
          }
          setLast(ev);
          onEventRef.current?.(ev);
        } catch { /* ignore parse errors */ }
      };
      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        if (!cancelled) {
          setTimeout(connect, retry);
          retry = Math.min(retry * 2, 30000);
        }
      };
      ws.onerror = () => { try { ws.close(); } catch { /* noop */ } };
    };

    connect();

    return () => {
      cancelled = true;
      try { wsRef.current?.close(); } catch { /* noop */ }
    };
  }, []);

  return { last, connected };
}
