import Link from 'next/link';
import { useRef, useState } from 'react';
import { FetchError } from '@/components/FetchError';
import { SkeletonText } from '@/components/Skeleton';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { welcomeRewardMessages } from '@/lib/i18n/welcomeReward';
import { formatRewardAmount, rewardBadgeKey, showsRewardReason, type WelcomeRewardStatus } from '@/lib/welcome-reward';
import type { ProfileRead } from './useProfileRead';
import { WelcomeApplication } from './WelcomeApplication';
import { WelcomeEmailProof } from './WelcomeEmailProof';
import { WelcomeStepper } from './WelcomeStepper';

function RewardDetails({ data }: { data: WelcomeRewardStatus }) {
  const tr = useTranslations(welcomeRewardMessages);
  const { locale } = useLocale();
  return <>
    <span className="inline-flex rounded-full border border-neutral-700 px-2.5 py-1 text-xs font-medium text-neutral-200">{tr(rewardBadgeKey(data))}</span>
    {data.amount_raw !== null && <p className="break-words text-2xl font-semibold tabular-nums text-white">{formatRewardAmount(data.amount_raw, locale)} <span className="text-base text-neutral-300">ASPORE</span></p>}
    {showsRewardReason(data) && <p className="text-sm leading-relaxed text-neutral-300">{tr(data.reason)}</p>}
    {data.state === 'application' && <p className="text-xs leading-relaxed text-neutral-400">{tr('conditional')}</p>}
    {data.amount_raw !== null && !data.progress?.payout && <p className="text-xs leading-relaxed text-neutral-400">{tr('separate')}</p>}
  </>;
}

/** Reward entitlements stay visibly separate from deposits and completed transfers. */
type Notice = 'confirmed' | 'walletSaved' | 'applied';

export function WelcomeReward({ read, token, hasPassword }: { read: ProfileRead<WelcomeRewardStatus>; token: string; hasPassword?: boolean }) {
  const tr = useTranslations(welcomeRewardMessages);
  const [notice, setNotice] = useState<Notice | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const data = read.state.kind === 'success' ? read.state.data : null;
  /** The refetch replaces the action that held focus, so focus moves to the stable heading and the result is announced. */
  const done = (kind: Notice) => { setNotice(kind); heading.current?.focus(); };
  return <section aria-labelledby="welcome-reward-title" className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 p-5 sm:p-6">
    <h2 id="welcome-reward-title" ref={heading} tabIndex={-1} className="text-lg font-semibold text-white">{tr('title')}</h2>
    <p aria-live="polite" aria-atomic="true" className={notice ? 'mt-3 text-sm text-neutral-200' : undefined}>{notice && tr(notice)}</p>
    {read.state.kind === 'loading' && <div role="status" aria-busy="true" className="mt-4 space-y-3 text-sm text-neutral-300"><p>{tr('loading')}</p><SkeletonText lines={2} /></div>}
    {read.state.kind === 'error' && <FetchError onRetry={read.retry} />}
    {read.state.kind === 'auth' && <Link href="/login" className="mt-3 inline-flex min-h-11 items-center text-sm text-violet-300 underline">{tr('signIn')}</Link>}
    {data && <div className="mt-4 space-y-3">
      <RewardDetails data={data} />
      {data.progress && <WelcomeStepper progress={data.progress} token={token} hasPassword={hasPassword}
        onApplied={value => { read.replace(value); done('applied'); }} onChanged={kind => { done(kind); read.retry(); }} />}
      {!data.progress && data.reason === 'not_applied' && <WelcomeApplication token={token} onApplied={value => { read.replace(value); done('applied'); }} />}
      {!data.progress && data.reason === 'email_required' && notice !== 'confirmed' && <WelcomeEmailProof token={token} onConfirmed={() => { done('confirmed'); read.retry(); }} />}
    </div>}
  </section>;
}
