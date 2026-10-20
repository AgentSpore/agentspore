"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { API_URL } from "./api";
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "./auth";

type ChatIdentity = { id: string; name: string };
type ProfileResult = { status: "ready"; user: ChatIdentity } | { status: "error" };
type ProfileRecord = { generation: number; token: string; revision: number; attempt: number; result: ProfileResult };
export type ChatIdentityState = { status: "signed-out" } | { status: "checking" }
  | { status: "error"; retry: () => void } | { status: "ready"; user: ChatIdentity };

function isChatIdentity(value: unknown): value is ChatIdentity {
  return typeof value === "object" && value !== null && "id" in value && "name" in value
    && typeof value.id === "string" && !!value.id && typeof value.name === "string";
}

/** Verify the current chat principal with a bounded, retryable session-owned attempt. */
export function useChatIdentity(token?: string | null): ChatIdentityState {
  const session = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const effectiveToken = token === undefined ? session.accessToken : token;
  const [profile, setProfile] = useState<ProfileRecord | null>(null);
  const [revision, setRevision] = useState(0);
  const attemptRef = useRef(0);
  const retry = () => {
    if (profile?.attempt !== attemptRef.current || profile.generation !== getAuthSnapshot().generation) return;
    attemptRef.current += 1;
    setRevision(current => current + 1);
  };

  useEffect(() => {
    if (!effectiveToken) return;
    const captured = { generation: session.generation, token: effectiveToken, revision, attempt: ++attemptRef.current };
    const controller = new AbortController();
    const commit = (result: ProfileResult) => {
      if (controller.signal.aborted || captured.attempt !== attemptRef.current
        || getAuthSnapshot().generation !== captured.generation) return;
      clearTimeout(deadline);
      setProfile({ ...captured, result });
    };
    const deadline = setTimeout(() => { commit({ status: "error" }); controller.abort(); }, 10_000);
    fetch(`${API_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${effectiveToken}` }, signal: controller.signal,
    }).then(async response => {
      if (!response.ok) throw new Error("Profile verification failed");
      const user: unknown = await response.json();
      if (!isChatIdentity(user)) throw new Error("Invalid profile response");
      commit({ status: "ready", user: { id: user.id, name: user.name } });
    }).catch(() => commit({ status: "error" }));
    return () => { clearTimeout(deadline); controller.abort(); };
  }, [session.generation, effectiveToken, revision]);

  if (session.generation < 0) return { status: "checking" };
  if (!effectiveToken) return { status: "signed-out" };
  if (profile?.generation !== session.generation || profile.token !== effectiveToken || profile.revision !== revision)
    return { status: "checking" };
  return profile.result.status === "ready" ? profile.result : { status: "error", retry };
}
