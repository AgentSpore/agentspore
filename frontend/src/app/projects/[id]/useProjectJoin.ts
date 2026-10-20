import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from '@/lib/auth';
import { publicMessages } from '@/lib/i18n/public';

type Result = 'youAreNowAContributor' | 'yourRequestIsPendingApproval' | 'projectJoinAlready' | 'projectJoinSignInAgain' | 'projectJoinFailed';
type Guard = (generation: number) => boolean;
type Request = (path: string, token?: string, options?: RequestInit) => Promise<Response>;
type Operation = { projectId: string; generation: number; guard: Guard; controller: AbortController };
type State = { operation: Operation; message: Result | null; pending: boolean };

async function joinResult(response: Response): Promise<Result> {
  if (response.status === 409) return 'projectJoinAlready';
  if (response.status === 401) return 'projectJoinSignInAgain';
  if (response.status !== 200) throw new Error('Project join failed');
  const value: unknown = await response.json();
  if (!value || typeof value !== 'object') throw new Error('Invalid project join response');
  const status: unknown = Reflect.get(value, 'status');
  if (status === 'auto_approved') return 'youAreNowAContributor';
  if (status === 'pending_approval') return 'yourRequestIsPendingApproval';
  throw new Error('Invalid project join status');
}

/** Own project join outcomes and singleflight requests within the current auth context. */
export function useProjectJoin(projectId: string, token: string | undefined, guard: Guard, request: Request, refresh: () => void) {
  const { generation } = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const pending = useRef<Operation | null>(null);
  const [state, setState] = useState<State | null>(null);
  useEffect(() => () => {
    pending.current?.controller.abort(); pending.current = null;
  }, [projectId, token, guard, generation]);
  const current = state?.operation.projectId === projectId && state.operation.generation === generation && state.operation.guard === guard;
  const message = current ? state.message : null;
  const owns = (operation: Operation | null) => operation !== null && pending.current === operation && guard(operation.generation);
  const join = async () => {
    if (!token || !guard(generation)) return;
    if (owns(pending.current)) return;
    pending.current?.controller.abort();
    const operation = { projectId, generation, guard, controller: new AbortController() };
    pending.current = operation; setState({ operation, message: null, pending: true });
    try {
      const response = await request(`/projects/${projectId}/contributors/join`, token, {
        method: 'POST', body: JSON.stringify({ message: publicMessages.en.iDLikeToContributeToThisProject }), signal: operation.controller.signal,
      });
      if (!owns(operation)) return;
      const result = await joinResult(response);
      if (!owns(operation)) return;
      setState({ operation, message: result, pending: false });
      if (result !== 'projectJoinSignInAgain') refresh();
    } catch {
      if (owns(operation)) setState({ operation, message: 'projectJoinFailed', pending: false });
    } finally {
      if (pending.current === operation) pending.current = null;
    }
  };
  return { join, message, pending: current && state.pending, ...joinPresentation(message, current && state.pending) };
}

function joinPresentation(message: Result | null, pending: boolean) {
  const signInAgain = message === 'projectJoinSignInAgain', error = signInAgain || message === 'projectJoinFailed';
  const role: 'alert' | 'status' = error ? 'alert' : 'status';
  const label: 'requesting' | 'projectJoinSignInAgain' | 'requestToJoin' = pending ? 'requesting' : signInAgain ? 'projectJoinSignInAgain' : 'requestToJoin';
  return { signInAgain, showAction: !message || error, role, label };
}
