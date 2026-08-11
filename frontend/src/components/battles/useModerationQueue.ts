import { useEffect, useState } from "react";
import { API_URL, ModerationTaskView } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";

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
 * screen; any other failure just releases the busy lock so the row is
 * retryable.
 */
export function useModerationQueue() {
  const [state, setState] = useState<ModerationLoadState>("loading");
  const [tasks, setTasks] = useState<ModerationTaskView[]>([]);
  const [pendingId, setPendingId] = useState<string | null>(null);

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
      // Leave the row in place; the moderator can retry the action.
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

  return { state, tasks, pendingId, approve, reject };
}
