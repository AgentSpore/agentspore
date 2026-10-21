import { useCallback, useEffect, useRef, useState } from 'react';
import { API_URL, type Project as ApiProject, type ProjectOwnership } from '@/lib/api';

// ─── Types ────────────────────────────────────────────────────────────────────
export type Project = Pick<ApiProject,
  'id' | 'title' | 'description' | 'category' | 'status' | 'repo_url' | 'deploy_url'
  | 'tech_stack' | 'agent_name' | 'agent_handle' | 'creator_agent_id'
  | 'votes_up' | 'votes_down' | 'created_at' | 'recovery_provenance'>;
export interface HumanContributor {
  id: string; role: string; contribution_points: number; joined_at: string;
  user_id: string; user_name: string; user_email: string; wallet_address: string | null;
}
type Contributors = { contributors: HumanContributor[] };
type Status = 'loading' | 'ready' | 'error' | 'not-found';
type ReadState<T> = { projectId: string; context: string; status: Status; data: T | null };
type Request = (path: string, token?: string, options?: RequestInit) => Promise<Response>;
type Attempt = { controller: AbortController };
const initial = <T,>(projectId: string, context = projectId): ReadState<T> => ({ projectId, context, status: 'loading', data: null });

function isProject(value: unknown, id: string): value is Project {
  if (!value || typeof value !== 'object') return false;
  const technologies: unknown = Reflect.get(value, 'tech_stack');
  return Reflect.get(value, 'id') === id && typeof Reflect.get(value, 'title') === 'string'
    && Array.isArray(technologies) && technologies.every(item => typeof item === 'string');
}
function hasContributors(value: unknown): value is Contributors & ProjectOwnership {
  return !!value && typeof value === 'object' && Array.isArray(Reflect.get(value, 'contributors'));
}

function useProjectOverview(projectId: string) {
  const [state, setState] = useState(() => initial<Project>(projectId));
  const pending = useRef<Attempt | null>(null);
  // Load project info
  const reload = useCallback(async () => {
    if (pending.current) return;
    const operation = { controller: new AbortController() }; pending.current = operation;
    setState(previous => ({ ...previous, status: 'loading' }));
    try {
      const response = await fetch(`${API_URL}/api/v1/projects/${projectId}`, { signal: operation.controller.signal });
      if (response.status === 404) {
        if (pending.current === operation) setState({ ...initial<Project>(projectId), status: 'not-found' });
        return;
      }
      if (!response.ok) throw new Error('Project read failed');
      const value: unknown = await response.json();
      if (!isProject(value, projectId)) throw new Error('Invalid project response');
      if (pending.current === operation) setState({ projectId, context: projectId, status: 'ready', data: value });
    } catch { if (pending.current === operation) setState(previous => ({ ...previous, status: 'error' })); }
    finally { if (pending.current === operation) pending.current = null; }
  }, [projectId]);
  useEffect(() => {
    if (!projectId) return;
    void reload();
    return () => { pending.current?.controller.abort(); pending.current = null; };
  }, [projectId, reload]);
  return { ...(state.projectId === projectId ? state : initial<Project>(projectId)), retry: reload };
}

function useProjectOwnership(projectId: string) {
  const [state, setState] = useState(() => initial<ProjectOwnership>(projectId));
  const pending = useRef<Attempt | null>(null);
  const reload = useCallback(async () => {
    if (pending.current) return;
    const operation = { controller: new AbortController() }; pending.current = operation;
    setState(previous => ({ ...previous, status: 'loading' }));
    try {
      const response = await fetch(`${API_URL}/api/v1/projects/${projectId}/ownership`, { signal: operation.controller.signal });
      if (!response.ok) throw new Error('Ownership read failed');
      const value: unknown = await response.json();
      if (!hasContributors(value)) throw new Error('Invalid ownership response');
      if (pending.current === operation) setState({ projectId, context: projectId, status: 'ready', data: value });
    } catch { if (pending.current === operation) setState(previous => ({ ...previous, status: 'error' })); }
    finally { if (pending.current === operation) pending.current = null; }
  }, [projectId]);
  useEffect(() => {
    if (!projectId) return;
    void reload();
    return () => { pending.current?.controller.abort(); pending.current = null; };
  }, [projectId, reload]);
  return { ...(state.projectId === projectId ? state : initial<ProjectOwnership>(projectId)), retry: reload };
}

function useProjectContributors(projectId: string, token: string | undefined, revision: number, request: Request) {
  const context = JSON.stringify([projectId, token, revision]);
  const [state, setState] = useState(() => initial<Contributors>(projectId, context));
  const pending = useRef<Attempt | null>(null);
  const reload = useCallback(async () => {
    if (pending.current) return;
    const operation = { controller: new AbortController() }; pending.current = operation;
    setState(previous => ({ projectId, context, data: previous.projectId === projectId ? previous.data : null, status: 'loading' }));
    try {
      const response = await request(`/projects/${projectId}/contributors`, token, { signal: operation.controller.signal });
      if (!response.ok) throw new Error('Contributors read failed');
      const value: unknown = await response.json();
      if (!hasContributors(value)) throw new Error('Invalid contributors response');
      if (pending.current === operation) setState({ projectId, context, status: 'ready', data: value });
    } catch { if (pending.current === operation) setState(previous => ({ ...previous, status: 'error' })); }
    finally { if (pending.current === operation) pending.current = null; }
  }, [context, projectId, request, token]);
  useEffect(() => {
    if (!projectId) return;
    void reload();
    return () => { pending.current?.controller.abort(); pending.current = null; };
  }, [projectId, reload]);
  const current = state.projectId === projectId ? state : initial<Contributors>(projectId, context);
  return { ...current, status: state.context === context ? current.status : 'loading', retry: reload };
}

/** Read the existing project, public ownership and session-selected contributor list independently. */
export function useProjectReads(projectId: string, token: string | undefined, revision: number, request: Request) {
  const project = useProjectOverview(projectId);
  const ownership = useProjectOwnership(projectId);
  const contributors = useProjectContributors(projectId, token, revision, request);
  return { project, ownership, contributors };
}
