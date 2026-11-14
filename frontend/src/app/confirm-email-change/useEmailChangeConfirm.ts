import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { API_URL } from '@/lib/api';
import { fetchWithAuth, getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from '@/lib/auth';
import { readEmailChangeStep } from '@/lib/welcome-email-change';

export type EmailChangeOutcome =
  'reading' | 'missing' | 'needSignIn' | 'working' | 'done' | 'approved' | 'invalid' | 'pending' | 'down' | 'network';

const STASH_KEY = 'email_change_link';
const LINK_TTL_MS = 600000;
const FINAL: Partial<Record<number, EmailChangeOutcome>> = { 400: 'invalid', 422: 'invalid', 409: 'pending' };

/** The link token lives only in the fragment, so it never reaches a server or a log; the fragment is dropped once read. */
function takeLinkToken(): string | null {
  const fromFragment = new URLSearchParams(window.location.hash.slice(1)).get('token');
  if (fromFragment) {
    stash(fromFragment);
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
    return fromFragment;
  }
  return readStash();
}

function readStash(): string | null {
  try {
    const raw = sessionStorage.getItem(STASH_KEY);
    const stored: unknown = raw && JSON.parse(raw);
    if (typeof stored === 'object' && stored !== null && 'token' in stored && 'until' in stored
      && typeof stored.token === 'string' && typeof stored.until === 'number' && stored.until > Date.now()) return stored.token;
    if (raw) sessionStorage.removeItem(STASH_KEY);
  } catch {
    // Unreadable or denied storage counts as no stashed link.
  }
  return null;
}

function stash(token: string | null) {
  try {
    if (token) sessionStorage.setItem(STASH_KEY, JSON.stringify({ token, until: Date.now() + LINK_TTL_MS }));
    else sessionStorage.removeItem(STASH_KEY);
  } catch {
    // Storage denied: the link still works while this tab stays on the page.
  }
}

async function postConfirm(linkToken: string): Promise<EmailChangeOutcome> {
  const response = await fetchWithAuth(`${API_URL}/api/v1/users/me/email/change/confirm`, {
    method: 'POST', signal: AbortSignal.timeout(20000),
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: linkToken }),
  });
  if (response.status === 401) return 'needSignIn';
  if (response.ok) return (await readEmailChangeStep(response, 'done')) === 'approve_old' ? 'approved' : 'done';
  return FINAL[response.status] ?? 'down';
}

/** Confirms one email-change link once the visitor is signed in; the link survives a sign-in round trip. */
export function useEmailChangeConfirm() {
  const auth = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const [link] = useState(() => (typeof window === 'undefined' ? null : takeLinkToken()));
  const [result, setResult] = useState<EmailChangeOutcome | null>(null);
  const [attempt, setAttempt] = useState(0);
  const started = useRef('');
  const mounted = useRef(true);
  const retry = useCallback(() => { setResult(null); setAttempt(count => count + 1); }, []);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    const key = `${attempt}:${auth.accessToken}`;
    if (!link || !auth.accessToken || started.current === key) return;
    started.current = key;
    const finish = (outcome: EmailChangeOutcome) => {
      if (!mounted.current) return;
      if (outcome !== 'network' && outcome !== 'needSignIn' && outcome !== 'down') stash(null);
      setResult(outcome);
    };
    postConfirm(link).then(finish, () => finish('network'));
  }, [link, auth.accessToken, attempt]);

  let outcome: EmailChangeOutcome = result ?? 'working';
  if (auth.generation < 0) outcome = 'reading';
  else if (!link) outcome = result ?? 'missing';
  else if (!auth.accessToken) outcome = 'needSignIn';
  return { outcome, retry };
}
