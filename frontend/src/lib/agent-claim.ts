import { API_URL } from './api';
import { fetchWithAuth } from './auth';

export const CLAIM_STATES = ['pending', 'claimed', 'canceled', 'denied', 'expired'] as const;
export type Claim = { claim_id: string; agent_id: string; agent_name: string; user_id: string; user_name: string; status: typeof CLAIM_STATES[number]; expires_at: string };
export type Claimant = { id: string; name: string; is_verified: boolean };
export class ClaimError extends Error { constructor(public status: number) { super('Claim request failed'); } }
function isObject(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === 'object' && !Array.isArray(value); }
function object(value: unknown): Record<string, unknown> {
  if (!isObject(value)) throw new Error('Invalid claim response');
  return value;
}
function text(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error('Invalid claim response');
  return value;
}
export function decodeClaim(value: unknown, agentId: string, userId: string): Claim {
  const row = object(value);
  const status = CLAIM_STATES.find(state => state === row.status);
  if (!status || row.agent_id !== agentId || row.user_id !== userId) throw new Error('Invalid claim identity');
  const expires_at = text(row.expires_at);
  if (!Number.isFinite(Date.parse(expires_at))) throw new Error('Invalid claim expiry');
  return { claim_id: text(row.claim_id), agent_id: agentId, agent_name: text(row.agent_name), user_id: userId, user_name: text(row.user_name), status, expires_at };
}
export async function claimRequest(path: string, options: RequestInit = {}): Promise<unknown> {
  const response = await fetchWithAuth(`${API_URL}/api/v1/${path}`, { ...options, cache: 'no-store', signal: options.signal ?? AbortSignal.timeout(12000) });
  if (!response.ok) throw new ClaimError(response.status);
  return response.json();
}
export function decodeClaimant(value: unknown): Claimant {
  const row = object(value);
  if (typeof row.is_verified !== 'boolean') throw new Error('Invalid account verification');
  return { id: text(row.id), name: text(row.name), is_verified: row.is_verified };
}
export async function readClaimAgent(agentId: string, signal: AbortSignal): Promise<string> {
  const response = await fetch(`${API_URL}/api/v1/agents/${encodeURIComponent(agentId)}`, { signal, cache: 'no-store' });
  if (!response.ok) throw new ClaimError(response.status);
  const row = object(await response.json());
  if (row.id !== agentId) throw new Error('Invalid agent identity');
  return text(row.name);
}
const RETURN_KEY = 'agent_claim_return';
export function claimReturnPath(path: string | null): string | null {
  return path && /^\/agents\/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\/claim$/i.test(path) ? path : null;
}
export function rememberClaimReturn(path: string): void {
  try {
    const safe = claimReturnPath(path);
    sessionStorage.removeItem(RETURN_KEY);
    if (safe) sessionStorage.setItem(RETURN_KEY, JSON.stringify({ path: safe, until: Date.now() + 600000 }));
  } catch { /* Login remains available when browser storage is disabled. */ }
}
export function consumeClaimReturn(): string | null {
  try {
    const stored = sessionStorage.getItem(RETURN_KEY);
    sessionStorage.removeItem(RETURN_KEY);
    const value = object(JSON.parse(stored ?? 'null'));
    return typeof value.until === 'number' && value.until > Date.now() ? claimReturnPath(typeof value.path === 'string' ? value.path : null) : null;
  } catch { return null; }
}
