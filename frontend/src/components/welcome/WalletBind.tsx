import Link from 'next/link';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { API_URL } from '@/lib/api';
import { fetchWithAuth } from '@/lib/auth';
import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { welcomeRewardMessages } from '@/lib/i18n/welcomeReward';

type Failure = 'walletTaken' | 'walletInvalid' | 'walletFailed' | 'signIn';
const ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

function failureOf(status: number): Failure {
  if (status === 409) return 'walletTaken';
  if (status === 401) return 'signIn';
  return status === 422 ? 'walletInvalid' : 'walletFailed';
}

/** Binds the payout wallet through the same endpoint as the profile's wallet section. */
export function WalletBind({ token, onBound }: { token: string; onBound: () => void }) {
  const tr = useTranslations(welcomeRewardMessages);
  const [address, setAddress] = useState('');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current) return;
    const value = address.trim();
    if (!ADDRESS.test(value)) { setFailure('walletInvalid'); return; }
    const controller = new AbortController();
    pending.current = controller; setBusy(true); setFailure(null);
    try {
      const response = await fetchWithAuth(`${API_URL}/api/v1/users/solana-wallet`, {
        method: 'PATCH', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]),
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ solana_wallet: value }),
      });
      if (controller.signal.aborted) return;
      if (response.ok) onBound(); else setFailure(failureOf(response.status));
    } catch {
      if (!controller.signal.aborted) setFailure('walletFailed');
    } finally {
      if (!controller.signal.aborted) { pending.current = null; setBusy(false); }
    }
  }

  return <form onSubmit={event => void submit(event)} className="space-y-3">
    <label htmlFor="welcome-wallet" className="block text-sm text-neutral-100">{tr('walletLabel')}</label>
    <input id="welcome-wallet" value={address} onChange={event => setAddress(event.target.value)} disabled={busy}
      autoComplete="off" autoCapitalize="none" spellCheck={false} required aria-invalid={failure === 'walletInvalid' || failure === 'walletTaken'}
      aria-describedby={`welcome-wallet-hint${failure ? ' welcome-wallet-notice' : ''}`}
      className="min-h-11 w-full min-w-0 rounded-lg border border-neutral-600 bg-neutral-950 px-3 py-2 font-mono text-sm text-white" />
    <p id="welcome-wallet-hint" className="text-xs text-neutral-300">{tr('walletHint')}</p>
    <button type="submit" disabled={busy} className="min-h-11 rounded-lg border border-neutral-600 px-4 py-2 text-sm text-neutral-100 hover:bg-neutral-800 disabled:opacity-50">
      {tr(busy ? 'walletSaving' : 'walletSave')}
    </button>
    {failure && <p id="welcome-wallet-notice" role="alert" className="text-sm text-red-300">
      {failure === 'signIn' ? <Link href="/login?next=/profile" className="underline">{tr('signIn')}</Link> : tr(failure)}
    </p>}
  </form>;
}
