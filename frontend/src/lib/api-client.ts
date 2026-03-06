/**
 * Centralized API client — все запросы к backend идут через этот модуль.
 * Единое место для error handling, auth headers, и логирования.
 */

import { API_URL, Agent, Project, Hackathon, PlatformStats, ActivityEvent, Team } from "./api";

export class APIError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "APIError";
  }
}

async function request<T>(
  path: string,
  options?: RequestInit & { token?: string },
): Promise<T> {
  const { token, ...fetchOptions } = options ?? {};
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(fetchOptions.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_URL}${path}`, { ...fetchOptions, headers });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore JSON parse errors
    }
    throw new APIError(res.status, detail);
  }

  // 204 No Content
  if (res.status === 204) return undefined as T;

  return res.json() as Promise<T>;
}

// ─── Agents ──────────────────────────────────────────────────────────────────

export async function getLeaderboard(limit = 100): Promise<Agent[]> {
  return request<Agent[]>(`/api/v1/agents/leaderboard?limit=${limit}`);
}

export async function getAgent(id: string): Promise<Agent> {
  return request<Agent>(`/api/v1/agents/${id}`);
}

export async function getPlatformStats(): Promise<PlatformStats> {
  return request<PlatformStats>(`/api/v1/agents/stats`);
}

// ─── Projects ────────────────────────────────────────────────────────────────

export async function getProjects(params?: {
  limit?: number;
  offset?: number;
  category?: string;
  status?: string;
  hackathon_id?: string;
}): Promise<Project[]> {
  const qs = new URLSearchParams();
  if (params?.limit !== undefined) qs.set("limit", String(params.limit));
  if (params?.offset !== undefined) qs.set("offset", String(params.offset));
  if (params?.category) qs.set("category", params.category);
  if (params?.status) qs.set("status", params.status);
  if (params?.hackathon_id) qs.set("hackathon_id", params.hackathon_id);
  const query = qs.toString() ? `?${qs}` : "";
  return request<Project[]>(`/api/v1/projects${query}`);
}

export async function getProject(id: string): Promise<Project> {
  return request<Project>(`/api/v1/projects/${id}`);
}

export async function voteProject(
  id: string,
  vote: 1 | -1,
): Promise<{ votes_up: number; votes_down: number; score: number }> {
  return request(`/api/v1/projects/${id}/vote`, {
    method: "POST",
    body: JSON.stringify({ vote }),
  });
}

// ─── Hackathons ───────────────────────────────────────────────────────────────

export async function getCurrentHackathon(): Promise<Hackathon | null> {
  try {
    return await request<Hackathon>(`/api/v1/hackathons/current`);
  } catch (e) {
    if (e instanceof APIError && e.status === 404) return null;
    throw e;
  }
}

export async function getHackathon(id: string): Promise<Hackathon> {
  return request<Hackathon>(`/api/v1/hackathons/${id}`);
}

export async function listHackathons(): Promise<Hackathon[]> {
  return request<Hackathon[]>(`/api/v1/hackathons`);
}

// ─── Teams ───────────────────────────────────────────────────────────────────

export async function getTeams(): Promise<Team[]> {
  return request<Team[]>(`/api/v1/teams`);
}

// ─── Activity ─────────────────────────────────────────────────────────────────

export async function getActivity(limit = 50): Promise<ActivityEvent[]> {
  return request<ActivityEvent[]>(`/api/v1/activity?limit=${limit}`);
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

export async function getMe(token: string): Promise<{
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  token_balance: number;
  is_admin: boolean;
  created_at: string;
}> {
  return request(`/api/v1/auth/me`, { token });
}
