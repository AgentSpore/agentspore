'use client';

import Link from 'next/link';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { API_URL } from '@/lib/api';
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from '@/lib/auth';
import { ClaimError, claimRequest, decodeClaim, decodeClaimant, readClaimAgent, type Claim, type Claimant } from '@/lib/agent-claim';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { agentClaimMessages } from '@/lib/i18n/agentClaim';
import { localeTag } from '@/lib/i18n/locale';
import { useCopyToClipboard } from '@/hooks/useCopyToClipboard';

const button = 'inline-flex min-h-11 items-center justify-center rounded-lg border border-neutral-600 px-4 py-2 text-sm font-medium hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300 disabled:opacity-50';
type View = 'loading' | 'ready' | 'login' | 'verify' | 'unavailable' | 'unknown' | 'conflict';
function failureView(error: unknown): View {
  if (!(error instanceof ClaimError)) return 'unknown';
  const statuses: Record<number, View> = { 409: 'conflict', 401: 'login', 403: 'verify' };
  return statuses[error.status] ?? 'unknown';
}
export function AgentClaim({ agentId }: { agentId: string }) {
  const session = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  return <ClaimSession key={`${agentId}:${session.generation}`} agentId={agentId} generation={session.generation} signedIn={Boolean(session.accessToken)} />;
}
function useClaimSession({ agentId, generation, signedIn }: { agentId: string; generation: number; signedIn: boolean }) {
  const [name, setName] = useState('');
  const [user, setUser] = useState<Claimant | null>(null);
  const [claim, setClaim] = useState<Claim | null>(null);
  const [view, setView] = useState<View>('loading');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const mounted = useRef(true);
  const pending = useRef(false);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    Promise.all([readClaimAgent(agentId, controller.signal), signedIn ? claimRequest('auth/me', { signal: controller.signal }).then(decodeClaimant) : null])
      .then(([agentName, person]) => {
        if (controller.signal.aborted || getAuthSnapshot().generation !== generation) return;
        setName(agentName); setUser(person); setView(!person ? 'login' : person.is_verified ? 'ready' : 'verify');
      }).catch(error => { if (!controller.signal.aborted && getAuthSnapshot().generation === generation) setView(error instanceof ClaimError && error.status === 401 ? 'login' : 'unavailable'); });
    return () => { mounted.current = false; controller.abort(); };
  }, [agentId, generation, signedIn, revision]);
  const current = () => mounted.current && getAuthSnapshot().generation === generation;
  async function act(action: 'create' | 'refresh' | 'cancel') {
    if (pending.current || !user?.is_verified || !current()) return;
    pending.current = true;
    setBusy(true);
    const suffix = claim ? `/${encodeURIComponent(claim.claim_id)}` : '';
    try {
      const value = await claimRequest(`users/me/agent-claims${action === 'create' ? '' : suffix}`, action === 'create'
        ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent_id: agentId }) }
        : { method: action === 'cancel' ? 'DELETE' : 'GET' });
      const result = decodeClaim(value, agentId, user.id);
      if (current()) { setClaim(result); setView('ready'); }
    } catch (error) {
      if (current()) setView(failureView(error));
    } finally { pending.current = false; if (current()) setBusy(false); }
  }
  return { name, user, claim, view, busy, act, retry: () => { setView('loading'); setRevision(value => value + 1); } };
}
function ClaimSession(props: { agentId: string; generation: number; signedIn: boolean }) {
  const { agentId } = props;
  const flow = useClaimSession(props);
  const { name, user, claim, view } = flow;
  const tr = useTranslations(agentClaimMessages);
  const { locale } = useLocale();
  const { state: copyState, copy } = useCopyToClipboard();
  const prompt = claim && user ? tr('prompt', { agentId, claimId: claim.claim_id, userId: user.id, endpoint: new URL(`${API_URL}/api/v1/agents/owner-claims/${claim.claim_id}`, window.location.origin).href }) : '';
  const status = view === 'ready' ? claim?.status ?? 'ready' : view;
  return <section aria-labelledby="claim-title" className="mx-auto max-w-2xl space-y-6">
    <div><h1 id="claim-title" className="text-3xl font-semibold tracking-tight">{tr('title')}</h1><p className="mt-3 leading-relaxed text-neutral-300">{tr('intro')}</p></div>
    <dl className="space-y-3 rounded-xl border border-neutral-700 bg-neutral-900 p-5">
      <div><dt className="text-sm text-neutral-400">{tr('agent')}</dt><dd className="mt-1 break-words font-medium">{name || '…'}</dd><dd className="mt-1 break-all font-mono text-xs text-neutral-400">{agentId}</dd></div>
      {user && <div><dt className="text-sm text-neutral-400">{tr('account')}</dt><dd className="mt-1">{user.name}</dd><dd className="mt-1 break-all font-mono text-xs text-neutral-400">{user.id}</dd></div>}
    </dl>
    <p role="status" aria-live="polite" className="leading-relaxed text-neutral-200">{tr(status)}</p>
    <ClaimActions flow={flow} agentId={agentId} />
    {claim?.status === 'pending' && view === 'ready' && <ClaimInstruction prompt={prompt} expires={new Intl.DateTimeFormat(localeTag(locale), { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(claim.expires_at))} copyState={copyState} onCopy={() => void copy(prompt)} />}
    <ClaimUpdates flow={flow} />
  </section>;
}
function ClaimActions({ flow, agentId }: { flow: ReturnType<typeof useClaimSession>; agentId: string }) {
  const tr = useTranslations(agentClaimMessages);
  if (flow.view === 'login') return <Link className={button} href={`/login?next=${encodeURIComponent(`/agents/${agentId}/claim`)}`}>{tr('login')}</Link>;
  if (flow.view === 'verify') return <Link className={button} href="/profile">{tr('profile')}</Link>;
  if (flow.view === 'unavailable') return <button className={button} onClick={flow.retry}>{tr('retry')}</button>;
  if (flow.view !== 'ready') return null;
  if (flow.claim?.status === 'claimed') return <Link className={button} href="/workspace">{tr('workspace')}</Link>;
  return flow.claim?.status === 'pending' ? null : <button className={`${button} bg-white text-black hover:bg-neutral-200`} disabled={flow.busy} onClick={() => void flow.act('create')}>{tr(flow.busy ? 'busy' : 'create')}</button>;
}
function ClaimUpdates({ flow }: { flow: ReturnType<typeof useClaimSession> }) {
  const tr = useTranslations(agentClaimMessages);
  if (flow.view === 'login' || flow.view === 'verify') return null;
  if (!flow.claim && !['unknown', 'conflict'].includes(flow.view)) return null;
  if (flow.claim?.status === 'claimed') return null;
  return <div className="flex flex-wrap gap-3">
    <button className={button} disabled={flow.busy} onClick={() => void flow.act(flow.claim ? 'refresh' : 'create')}>{tr(flow.busy ? 'busy' : flow.claim ? 'refresh' : 'retry')}</button>
    {flow.claim?.status === 'pending' && <button className={button} disabled={flow.busy} onClick={() => void flow.act('cancel')}>{tr('cancel')}</button>}
  </div>;
}
function ClaimInstruction({ prompt, expires, copyState, onCopy }: { prompt: string; expires: string; copyState: string; onCopy: () => void }) {
  const tr = useTranslations(agentClaimMessages);
  return <div className="space-y-3 rounded-xl border border-violet-400/40 p-5">
    <label htmlFor="claim-instruction" className="block font-medium">{tr('instruction')}</label>
    <textarea id="claim-instruction" readOnly value={prompt} rows={9} className="w-full resize-y rounded-lg border border-neutral-600 bg-neutral-900 p-3 text-sm leading-relaxed text-neutral-200 focus-visible:outline-2 focus-visible:outline-violet-300" />
    <p className="text-sm text-neutral-400">{tr('expires')}: {expires}</p>
    <button className={button} onClick={onCopy}>{tr('copy')}</button>
    <p role="status" className="text-sm text-neutral-300">{copyState === 'copied' ? tr('copied') : copyState === 'failed' ? tr('copyFailed') : ''}</p>
  </div>;
}
