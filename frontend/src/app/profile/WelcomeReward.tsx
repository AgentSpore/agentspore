import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { API_URL } from '@/lib/api';
import { FetchError } from '@/components/FetchError';
import { SkeletonText } from '@/components/Skeleton';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { welcomeRewardMessages } from '@/lib/i18n/welcomeReward';
import { decodeWelcomeReward, formatRewardAmount, type WelcomeRewardStatus } from '@/lib/welcome-reward';
import type { ProfileRead } from './useProfileRead';
import { WelcomeEmailProof } from './WelcomeEmailProof';

const STATE_LABELS = {
  disabled: 'disabled', not_applied: 'state_not_applied', application: 'application',
  frozen: 'frozen', available: 'state_available', denied: 'state_denied',
} as const;

/** Explicit recovery never turns a profile read into a reward mutation. */
function WelcomeApplication({ token, onApplied }: { token: string; onApplied: (value: WelcomeRewardStatus) => void }) {
  const tr = useTranslations(welcomeRewardMessages);
  const pending = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => () => pending.current?.abort(), []);
  async function apply() {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller; setBusy(true); setFailed(false);
    try {
      const response = await fetch(`${API_URL}/api/v1/users/me/rewards/welcome/application`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}` },
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]),
      });
      if (!response.ok) throw new Error('Application unavailable');
      const data = decodeWelcomeReward(await response.json());
      if (!controller.signal.aborted) onApplied(data);
    } catch {
      if (!controller.signal.aborted) setFailed(true);
    } finally {
      if (!controller.signal.aborted) { pending.current = null; setBusy(false); }
    }
  }
  return <div className="space-y-3">
    <button type="button" disabled={busy} onClick={() => void apply()} className="min-h-11 rounded-lg border border-neutral-600 px-4 py-2 text-sm text-neutral-100 hover:bg-neutral-800 disabled:opacity-50">{tr(busy ? 'applying' : 'apply')}</button>
    {failed && <p role="alert" className="text-sm text-red-300">{tr('applyFailed')}</p>}
  </div>;
}

function RewardDetails({ data }: { data: WelcomeRewardStatus }) {
  const tr = useTranslations(welcomeRewardMessages);
  const { locale } = useLocale();
  return <>
    <span className="inline-flex rounded-full border border-neutral-700 px-2.5 py-1 text-xs font-medium text-neutral-200">{tr(STATE_LABELS[data.state])}</span>
    {data.amount_raw !== null && <p className="break-words text-2xl font-semibold tabular-nums text-white">{formatRewardAmount(data.amount_raw, locale)} <span className="text-base text-neutral-300">ASPORE</span></p>}
    <p className="text-sm leading-relaxed text-neutral-300">{tr(data.reason)}</p>
    {data.state === 'application' && <p className="text-xs leading-relaxed text-neutral-400">{tr('conditional')}</p>}
    {data.amount_raw !== null && <p className="text-xs leading-relaxed text-neutral-400">{tr('separate')}</p>}
  </>;
}

/** Reward entitlements stay visibly separate from deposits and completed transfers. */
export function WelcomeReward({ read, token }: { read: ProfileRead<WelcomeRewardStatus>; token: string }) {
  const tr = useTranslations(welcomeRewardMessages);
  const [confirmed, setConfirmed] = useState(false);
  const data = read.state.kind === 'success' ? read.state.data : null;
  return <section aria-labelledby="welcome-reward-title" className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 p-5 sm:p-6">
    <h2 id="welcome-reward-title" className="text-lg font-semibold text-white">{tr('title')}</h2>
    {confirmed && <p role="status" className="mt-3 text-sm text-neutral-200">{tr('confirmed')}</p>}
    {read.state.kind === 'loading' && <div role="status" aria-busy="true" className="mt-4 space-y-3 text-sm text-neutral-300"><p>{tr('loading')}</p><SkeletonText lines={2} /></div>}
    {read.state.kind === 'error' && <FetchError onRetry={read.retry} />}
    {read.state.kind === 'auth' && <Link href="/login" className="mt-3 inline-flex min-h-11 items-center text-sm text-violet-300 underline">{tr('signIn')}</Link>}
    {data && <div className="mt-4 space-y-3">
      <RewardDetails data={data} />
      {data.reason === 'not_applied' && <WelcomeApplication token={token} onApplied={read.replace} />}
      {data.reason === 'email_required' && !confirmed && <WelcomeEmailProof token={token} onConfirmed={() => { setConfirmed(true); read.retry(); }} />}
    </div>}
  </section>;
}
