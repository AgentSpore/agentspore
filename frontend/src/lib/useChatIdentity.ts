"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { API_URL } from "./api";
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "./auth";

type ChatIdentity = { id: string; name: string };
type ConfirmedProfile = { generation: number; token: string; user: ChatIdentity };

function isChatIdentity(value: unknown): value is ChatIdentity {
  return typeof value === "object" && value !== null && "id" in value && "name" in value
    && typeof value.id === "string" && !!value.id && typeof value.name === "string";
}

/** Return the server-confirmed chat author only for the current session and token. */
export function useChatIdentity(token?: string | null): ChatIdentity | null {
  const session = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const effectiveToken = token === undefined ? session.accessToken : token;
  const [profile, setProfile] = useState<ConfirmedProfile | null>(null);

  useEffect(() => {
    if (!effectiveToken) return;
    const captured = getAuthSnapshot();
    const controller = new AbortController();
    fetch(`${API_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${effectiveToken}` }, signal: controller.signal,
    }).then(async response => {
      const user: unknown = response.ok ? await response.json() : null;
      if (controller.signal.aborted || getAuthSnapshot().generation !== captured.generation) return;
      if (!isChatIdentity(user)) return;
      setProfile({ generation: captured.generation, token: effectiveToken, user: { id: user.id, name: user.name } });
    }).catch(() => { /* An unconfirmed profile grants no chat actions. */ });
    return () => controller.abort();
  }, [session.generation, effectiveToken]);

  return effectiveToken && profile?.generation === session.generation && profile.token === effectiveToken
    ? profile.user : null;
}
