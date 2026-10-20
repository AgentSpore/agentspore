import { FetchError } from '@/components/FetchError';
import { SkeletonText } from '@/components/Skeleton';
import { accountMessages } from '@/lib/i18n/account';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { localeTag } from '@/lib/i18n/locale';
import type { ProfileRead } from './useProfileRead';

/** Show internal deposit credits independently of wallet and payout history. */
export function DepositBalance({ read }: { read: ProfileRead<number> }) {
  const tr = useTranslations(accountMessages);
  const { locale } = useLocale();
  return <section aria-labelledby="deposit-balance-title" className="space-y-3 rounded-xl border border-neutral-800/50 bg-neutral-900/30 p-5 font-mono">
    <h2 id="deposit-balance-title" className="text-lg font-semibold text-white">{tr('depositBalance')}</h2>
    <p className="text-sm text-neutral-300">{tr('depositBalanceNote')}</p>
    {read.state.kind === 'loading' ? <div role="status" aria-busy="true" className="space-y-3 text-sm text-neutral-300"><p>{tr('loadingBalance')}</p><SkeletonText lines={2} /></div>
      : read.state.kind === 'success' ? <div><div className="text-2xl font-bold text-white">{read.state.data.toLocaleString(localeTag(locale))}</div><span className="text-sm text-violet-300">$ASPORE</span></div>
      : <FetchError onRetry={read.retry} />}
  </section>;
}
