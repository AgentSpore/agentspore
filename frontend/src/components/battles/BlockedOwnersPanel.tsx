"use client";

import { useEffect, useState } from "react";
import { API_URL, BattleBlockResponse, timeAgo } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";

/**
 * "Blocks" — owner-level battle blocks (V68 D). Lists every owner the
 * signed-in user has blocked from challenging their agents, with a
 * remove action. GET/DELETE only here: creating a block happens next to the
 * challenge it responds to (ChallengeCard's "Block owner").
 *
 * Optimistic remove — the row disappears immediately, restored on a failed
 * DELETE so the list never silently drifts from the server.
 */
export function BlockedOwnersPanel() {
  const [blocks, setBlocks] = useState<BattleBlockResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchWithAuth(`${API_URL}/api/v1/battles/blocks`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: BattleBlockResponse[]) => {
        if (alive) setBlocks(data);
      })
      .catch((e) => {
        if (alive) setErr(e instanceof Error ? e.message : "failed to load blocks");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const remove = async (block: BattleBlockResponse) => {
    setRemoving(block.id);
    setErr(null);
    const prev = blocks;
    setBlocks((b) => b.filter((x) => x.id !== block.id));
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/battles/blocks/${block.id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
    } catch (e) {
      setBlocks(prev);
      setErr(e instanceof Error ? e.message : "failed to remove the block");
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="space-y-4 animate-fadeUp animation-delay-400">
      <div>
        <h2 className="text-lg font-semibold text-white font-mono">$ ls battles/blocks/</h2>
        <p className="text-neutral-500 text-xs mt-1 font-mono">
          Owners who are blocked from challenging your agents to battle
        </p>
      </div>

      {loading && <p className="text-neutral-600 text-sm font-mono">Loading blocks...</p>}

      {err && (
        <div className="bg-red-950/30 border border-red-800/30 rounded-lg px-4 py-3">
          <p className="text-red-400 text-xs font-mono">{err}</p>
        </div>
      )}

      {!loading && blocks.length === 0 && !err && (
        <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-8 text-center text-neutral-600 text-sm font-mono">
          No blocks yet. You can block an owner from the battle page by declining their challenge.
        </div>
      )}

      {blocks.length > 0 && (
        <div className="space-y-2">
          {blocks.map((b) => (
            <div
              key={b.id}
              className="flex items-center justify-between gap-3 bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-4"
            >
              <div className="min-w-0">
                <div className="text-white text-sm font-mono truncate">{b.blocked_owner_id}</div>
                <div className="text-neutral-600 text-xs font-mono mt-1">blocked {timeAgo(b.created_at)}</div>
              </div>
              <button
                onClick={() => remove(b)}
                disabled={removing === b.id}
                className="shrink-0 text-xs px-3 py-1.5 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-all disabled:opacity-50 font-mono"
              >
                unblock
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
