import { useCallback, useEffect, useRef, useState } from 'react';
import { API_URL, type TeamDetail } from '@/lib/api';

type TeamProfile = Omit<TeamDetail, 'member_count' | 'project_count'>;
type Status = 'loading' | 'ready' | 'not-found' | 'error';
type State = { teamId: string; status: Status; data: TeamProfile | null };
type Operation = { teamId: string; controller: AbortController };
const initial = (teamId: string): State => ({ teamId, status: 'loading', data: null });

function isTeamProfile(value: unknown, teamId: string): value is TeamProfile {
  if (!value || typeof value !== 'object') return false;
  return Reflect.get(value, 'id') === teamId && typeof Reflect.get(value, 'name') === 'string'
    && Array.isArray(Reflect.get(value, 'members')) && Array.isArray(Reflect.get(value, 'projects'));
}

/** Own the anonymous team detail read and discard outcomes from replaced attempts. */
export function useTeamDetails(teamId: string) {
  const [state, setState] = useState(() => initial(teamId));
  const pending = useRef<Operation | null>(null);
  const retry = useCallback(async () => {
    if (pending.current) return;
    const operation = { teamId, controller: new AbortController() }; pending.current = operation;
    const owns = () => pending.current === operation && operation.teamId === teamId;
    setState(initial(teamId));
    try {
      const response = await fetch(`${API_URL}/api/v1/teams/${teamId}`, { signal: operation.controller.signal });
      if (!owns()) return;
      if (response.status === 404) { setState({ teamId, status: 'not-found', data: null }); return; }
      if (!response.ok) throw new Error('Team detail read failed');
      const value: unknown = await response.json();
      if (!isTeamProfile(value, teamId)) throw new Error('Invalid team detail response');
      if (owns()) setState({ teamId, status: 'ready', data: value });
    } catch {
      if (owns()) setState({ teamId, status: 'error', data: null });
    } finally { if (pending.current === operation) pending.current = null; }
  }, [teamId]);
  useEffect(() => {
    void retry();
    return () => { pending.current?.controller.abort(); pending.current = null; };
  }, [retry]);
  return { ...(state.teamId === teamId ? state : initial(teamId)), retry };
}
