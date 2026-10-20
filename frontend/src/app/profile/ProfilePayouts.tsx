import { FetchError } from '@/components/FetchError';
import { SkeletonText } from '@/components/Skeleton';
import type { TokenPayout } from '@/lib/api';
import { accountMessages } from '@/lib/i18n/account';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { localeTag } from '@/lib/i18n/locale';
import type { ProfileRead } from './useProfileRead';

const STATUS_CLASSES = {
  pending: 'bg-amber-400/10 text-amber-400 border-amber-400/20',
  sent: 'bg-blue-400/10 text-blue-400 border-blue-400/20',
  confirmed: 'bg-emerald-400/10 text-emerald-400 border-emerald-400/20',
  failed: 'bg-red-400/10 text-red-400 border-red-400/20',
};

function PayoutRow({ payout }: { payout: TokenPayout }) {
  const tr = useTranslations(accountMessages);
  const { locale } = useLocale();
  return <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 p-4 font-mono">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-white">{payout.amount.toLocaleString(localeTag(locale))} $ASPORE</div>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-neutral-300">
          <span>{payout.period_start} — {payout.period_end}</span><span aria-hidden="true">·</span>
          <span>{new Intl.NumberFormat(localeTag(locale)).format(payout.contribution_points)} {tr('pts')}</span>
        </div>
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {payout.tx_signature && <a href={`https://solscan.io/tx/${payout.tx_signature}`} target="_blank" rel="noopener noreferrer"
          className="inline-flex min-h-11 min-w-11 items-center justify-center text-xs text-neutral-300 hover:text-cyan-400">{tr('tx')}</a>}
        <span className={`rounded-full border px-2 py-0.5 text-xs ${STATUS_CLASSES[payout.status]}`}>{tr(payout.status)}</span>
      </div>
    </div>
  </div>;
}

/** Keep loading, failed, empty and populated history visible in one section. */
export function ProfilePayouts({ read }: { read: ProfileRead<TokenPayout[]> }) {
  const tr = useTranslations(accountMessages);
  return <section aria-labelledby="payout-history-title" className="space-y-4">
    <div><h2 id="payout-history-title" className="text-lg font-semibold text-white font-mono">{tr('catPayoutsLog')}</h2>
      <p className="mt-1 text-sm text-neutral-300 font-mono">{tr('monthlyASPOREDistributions')}</p></div>
    {read.state.kind === 'loading' ? <div role="status" aria-busy="true" className="space-y-3 text-sm text-neutral-300 font-mono"><p>{tr('loadingPayouts')}</p><SkeletonText lines={3} /></div>
      : read.state.kind !== 'success' ? <FetchError onRetry={read.retry} />
      : read.state.data.length === 0 ? <p className="rounded-xl border border-neutral-800/50 p-5 text-sm text-neutral-300 font-mono">{tr('noPayouts')}</p>
      : <div className="space-y-2">{read.state.data.map(payout => <PayoutRow key={payout.id} payout={payout} />)}</div>}
  </section>;
}
