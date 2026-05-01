"use client";

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { API_URL } from "./api";

export type RealtimeEvent =
  | { type: "hello"; user_id: string }
  | { type: "ping" }
  | { type: "hosted_agent_status"; hosted_id: string; agent_id: string | null; status: string }
  | { type: "owner_message"; hosted_id: string; role: string; content: string }
  | { type: "notification"; text: string }
  | { type: string; [k: string]: unknown };

type Listener = (e: RealtimeEvent) => void;

/* ─── Shared context ─────────────────────────────────────────────────────── */

interface RealtimeUserCtx {
  /** Subscribe to realtime events. Returns an unsubscribe function. */
  subscribe: (fn: Listener) => () => void;
  connected: boolean;
}

const RealtimeUserContext = createContext<RealtimeUserCtx | null>(null);

/* ─── Provider: owns exactly one WebSocket ──────────────────────────────── */

/**
 * Mount once in a layout or page root. All useRealtimeUser() calls below it
 * share the single WS connection.
 */
export function RealtimeUserProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const listenersRef = useRef<Set<Listener>>(new Set());
  const wsRef = useRef<WebSocket | null>(null);

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
          listenersRef.current.forEach((fn) => fn(ev));
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

  const subscribe = (fn: Listener) => {
    listenersRef.current.add(fn);
    return () => { listenersRef.current.delete(fn); };
  };

  return (
    <RealtimeUserContext.Provider value={{ subscribe, connected }}>
      {children}
    </RealtimeUserContext.Provider>
  );
}

/* ─── Hook: subscriber, no new WS ──────────────────────────────────────── */

/**
 * Subscribe to the shared user WebSocket. Must be used inside a
 * <RealtimeUserProvider>. Multiple calls share one connection.
 *
 * Returns { last, connected } for components that prefer state over callbacks.
 */
export function useRealtimeUser(onEvent?: Listener) {
  const ctx = useContext(RealtimeUserContext);
  const [last, setLast] = useState<RealtimeEvent | null>(null);

  // Keep a stable ref so the subscription closure always calls the latest handler
  const onEventRef = useRef<Listener | undefined>(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!ctx) return;
    const unsub = ctx.subscribe((ev) => {
      setLast(ev);
      onEventRef.current?.(ev);
    });
    return unsub;
  }, [ctx]);

  return { last, connected: ctx?.connected ?? false };
}
