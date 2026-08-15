import { useEffect, useState } from "react";
import { API_URL, ModerationTaskView } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";

/** The server caps its queue at 100 (battle_repo.list_moderation_queue); the
 *  page says so rather than implying the list is everything. */
export const MODERATION_PAGE_LIMIT = 100;

export type ModerationLoadState = "loading" | "unauthorized" | "error" | "ready";

async function loadQueue(): Promise<{ state: ModerationLoadState; tasks: ModerationTaskView[] }> {
  const res = await fetchWithAuth(`${API_URL}/api/v1/battles/tasks/moderation`);
  if (res.status === 401 || res.status === 403) return { state: "unauthorized", tasks: [] };
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return { state: "ready", tasks: (await res.json()) as ModerationTaskView[] };
}

/**
 * Owns the moderation queue's state + admin actions. A 409 (task no longer
 * awaiting approval) refetches the queue instead of leaving a stale row on
 * screen. Any other failure is SURFACED per row rather than swallowed: a
 * silent failure on a page whose whole purpose is the side effect reads as a
 * mis-click, and the admin clicks again on a row that never moved.
 */
export function useModerationQueue() {
  const [state, setState] = useState<ModerationLoadState>("loading");
  const [tasks, setTasks] = useState<ModerationTaskView[]>([]);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);

  const refetch = () => {
    loadQueue()
      .then((r) => {
        setState(r.state);
        setTasks(r.tasks);
      })
      .catch(() => setState("error"));
  };

  useEffect(refetch, []);

  const runAction = async (id: string, action: "approve" | "reject", body?: object) => {
    setPendingId(id);
    setFailedId(null);
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/battles/tasks/${id}/${action}`, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (res.status === 409) {
        refetch();
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setTasks((prev) => prev.filter((t) => t.id !== id));
    } catch {
      // Row stays put AND says so. Dropping it silently is what makes a
      // failed approve indistinguishable from a mis-click.
      setFailedId(id);
    } finally {
      setPendingId(null);
    }
  };

  const approve = (id: string) => runAction(id, "approve");

  const reject = (id: string) => {
    const reason = window.prompt("Reason for rejecting this task:");
    if (!reason || !reason.trim()) return;
    runAction(id, "reject", { reason: reason.trim() });
  };

  return { state, tasks, pendingId, failedId, approve, reject, refetch };
}
