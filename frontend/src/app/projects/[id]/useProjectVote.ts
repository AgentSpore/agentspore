import { useEffect, useRef, useState } from 'react';
import { API_URL } from '@/lib/api';

type VoteCounts = { project_id: string; votes_up: number; votes_down: number };
function isCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}
function isVoteCounts(value: unknown, projectId: string): value is VoteCounts {
  return !!value && typeof value === 'object' && Reflect.get(value, 'project_id') === projectId
    && isCount(Reflect.get(value, 'votes_up')) && isCount(Reflect.get(value, 'votes_down'));
}

/** Own a project's anonymous vote operation and canonical counters across local section changes. */
export function useProjectVote(projectId: string, initial: { votes_up: number; votes_down: number } | null) {
  const [accepted, setAccepted] = useState<VoteCounts | null>(null);
  const [voting, setVoting] = useState(false);
  const [error, setError] = useState<'wait' | 'error' | null>(null);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => { pending.current?.abort(); pending.current = null; }, [projectId]);

  const vote = async (value: 1 | -1) => {
    if (pending.current) return;
    const operation = new AbortController(); pending.current = operation;
    setVoting(true); setError(null);
    try {
      const response = await fetch(`${API_URL}/api/v1/projects/${projectId}/vote`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ vote: value }), signal: operation.signal,
      });
      if (pending.current !== operation) return;
      if (!response.ok) { setError(response.status === 429 ? 'wait' : 'error'); return; }
      const body: unknown = await response.json();
      if (pending.current !== operation) return;
      if (!isVoteCounts(body, projectId)) { setError('error'); return; }
      setAccepted(body);
    } catch { if (pending.current === operation) setError('error'); }
    finally { if (pending.current === operation) { pending.current = null; setVoting(false); } }
  };
  return { up: accepted?.votes_up ?? initial?.votes_up ?? 0, down: accepted?.votes_down ?? initial?.votes_down ?? 0, voting, error, vote };
}
