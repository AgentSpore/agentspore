import { useEffect, useState } from 'react';
import { API_URL, type TokenPayout } from '@/lib/api';

type ReadState<T> = { kind: 'loading' | 'error' | 'auth' } | { kind: 'success'; data: T };
/** Private profile reads share a lifecycle, with independent retries and no cache. */
export interface ProfileRead<T> {
  state: ReadState<T>;
  retry: () => void;
  replace: (data: T) => void;
}
/** Fields returned by auth/me, plus its optional administrative label. */
export interface ProfileUser {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  token_balance: number;
  solana_wallet: string | null;
  is_admin: boolean;
  created_at: string;
}

/** Capture one session's headers and discard responses after cleanup or retry. */
export function useProfileRead<T>(path: string, token: string | null, decode: (value: unknown) => T, initializing = false): ProfileRead<T> {
  const [revision, setRevision] = useState(0);
  const [stored, setStored] = useState<{ revision: number; state: ReadState<T> }>({ revision: 0, state: { kind: 'loading' } });
  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    const save = (state: ReadState<T>) => {
      if (!controller.signal.aborted) setStored({ revision, state });
    };
    void (async () => {
      try {
        const response = await fetch(`${API_URL}/api/v1/${path}`, {
          headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
        });
        if (response.status === 401) { save({ kind: 'auth' }); return; }
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        save({ kind: 'success', data: decode(await response.json()) });
      } catch {
        save({ kind: 'error' });
      }
    })();
    return () => controller.abort();
  }, [path, token, decode, revision]);
  const state = !token && !initializing ? { kind: 'auth' as const }
    : stored.revision === revision ? stored.state : { kind: 'loading' as const };
  return { state, retry: () => setRevision(value => value + 1), replace: data => setStored({ revision, state: { kind: 'success', data } }) };
}

function isProfileObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readObject(value: unknown): Record<string, unknown> {
  if (!isProfileObject(value)) throw new Error('Invalid profile response');
  return value;
}

function readText(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Invalid profile text');
  return value;
}

function readNullableText(value: unknown): string | null {
  return value === null ? null : readText(value);
}

function readUnits(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('Invalid profile amount');
  return value;
}

/** Decode the existing account DTO without inventing a deposit balance. */
export function decodeProfile(value: unknown): ProfileUser {
  const row = readObject(value);
  return {
    id: readText(row.id), name: readText(row.name), email: readText(row.email),
    avatar_url: readNullableText(row.avatar_url), solana_wallet: readNullableText(row.solana_wallet),
    token_balance: readUnits(row.token_balance), created_at: readText(row.created_at), is_admin: row.is_admin === true,
  };
}

/** Only verified whole deposit credits, including zero, become displayable data. */
export function decodeDepositBalance(value: unknown): number {
  return readUnits(readObject(value).aspore_balance);
}

function decodePayout(value: unknown): TokenPayout {
  const row = readObject(value);
  const status = row.status;
  if (status !== 'pending' && status !== 'sent' && status !== 'confirmed' && status !== 'failed') throw new Error('Invalid payout status');
  const amount = readUnits(row.amount);
  if (amount === 0) throw new Error('Invalid payout amount');
  return {
    id: readText(row.id), amount, status, contribution_points: readUnits(row.contribution_points),
    pool_total: readUnits(row.pool_total), period_start: readText(row.period_start), period_end: readText(row.period_end),
    tx_signature: readNullableText(row.tx_signature), created_at: readText(row.created_at),
    sent_at: readNullableText(row.sent_at), confirmed_at: readNullableText(row.confirmed_at),
  };
}

/** Validate the selected payout fields and the database's four admitted statuses. */
export function decodePayouts(value: unknown): TokenPayout[] {
  if (!Array.isArray(value)) throw new Error('Invalid payout history');
  return value.map(decodePayout);
}
