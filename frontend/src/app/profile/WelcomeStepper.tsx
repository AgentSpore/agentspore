import { API_URL } from '@/lib/api';
import { EmailChangeForm } from '@/components/welcome/EmailChangeForm';
import { WalletBind } from '@/components/welcome/WalletBind';
import { localeTag } from '@/lib/i18n/locale';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { welcomeRewardMessages } from '@/lib/i18n/welcomeReward';
import type { WelcomeProgress } from '@/lib/welcome-progress';
import type { WelcomeRewardStatus } from '@/lib/welcome-reward';
import { WelcomeApplication } from './WelcomeApplication';
import { WelcomeEmailProof } from './WelcomeEmailProof';

const BUTTON_LINK = 'inline-flex min-h-11 items-center rounded-lg border border-neutral-600 px-4 py-2 text-sm text-neutral-100 hover:bg-neutral-800';
interface Props { progress: WelcomeProgress; token: string; onApplied: (value: WelcomeRewardStatus) => void; onChanged: () => void }

function Steps({ progress }: { progress: WelcomeProgress }) {
  const tr = useTranslations(welcomeRewardMessages);
  const current = progress.steps.find(step => !step.done)?.id;
  return <ol aria-label={tr('stepsLabel')} className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-5">
    {progress.steps.map(step => <li key={step.id} aria-current={step.id === current ? 'step' : undefined}
      className={`min-w-0 rounded-lg border px-3 py-2 text-sm ${step.id === current ? 'border-violet-400 text-white' : 'border-neutral-700 text-neutral-200'}`}>
      <span className="block break-words">{tr(`step_${step.id}`)}</span>
      <span className="block text-xs text-neutral-300">{tr(step.done ? 'stepDone' : step.id === current ? 'stepCurrent' : 'stepLater')}</span>
    </li>)}
  </ol>;
}

function Payout({ payout }: { payout: NonNullable<WelcomeProgress['payout']> }) {
  const tr = useTranslations(welcomeRewardMessages);
  const text = { pending: 'payoutPending', sent: 'payoutSent', confirmed: 'payoutConfirmed' } as const;
  return <div role="status" className="space-y-2 text-sm text-neutral-100">
    <p>{tr(text[payout.state])}</p>
    {payout.state === 'confirmed' && payout.signature &&
      <a href={`https://solscan.io/tx/${payout.signature}`} target="_blank" rel="noopener noreferrer" className={BUTTON_LINK}>{tr('viewTx')}</a>}
  </div>;
}

function Holds({ progress }: { progress: WelcomeProgress }) {
  const tr = useTranslations(welcomeRewardMessages);
  const { locale } = useLocale();
  if (progress.holds.length === 0) return null;
  const date = progress.waitUntil && new Intl.DateTimeFormat(localeTag(locale), { dateStyle: 'long', timeZone: 'UTC' }).format(progress.waitUntil);
  return <div className="space-y-2">
    <h3 className="text-sm font-medium text-white">{tr('holdsTitle')}</h3>
    <ul className="list-disc space-y-1 pl-5 text-sm text-neutral-200">
      {progress.holds.map(hold => <li key={hold}>{tr(`hold_${hold}`)}{hold === 'github_too_new' && date && <> {tr('waitUntil', { date })}</>}</li>)}
    </ul>
  </div>;
}

function NextAction({ progress, token, onApplied, onChanged }: Props) {
  const tr = useTranslations(welcomeRewardMessages);
  switch (progress.nextStep) {
    case 'confirm_email': return <WelcomeEmailProof token={token} onConfirmed={onChanged} />;
    case 'change_email': return <div className="space-y-2"><h3 className="text-sm font-medium text-white">{tr('changeEmailTitle')}</h3><EmailChangeForm token={token} /></div>;
    case 'bind_wallet': return <WalletBind token={token} onBound={onChanged} />;
    case 'apply': return <WelcomeApplication token={token} onApplied={onApplied} />;
    case 'relink_github': return progress.githubRelinkPath
      ? <a href={`${API_URL}${progress.githubRelinkPath}`} className={BUTTON_LINK}>{tr('relink')}</a> : null;
    case 'wait_review': return <p className="text-sm text-neutral-200">{tr('next_wait_review')}</p>;
    case 'wait_payout': return progress.payout ? null : <p className="text-sm text-neutral-200">{tr('next_wait_payout')}</p>;
    default: return null;
  }
}

/** Five steps, what holds the reward in plain words, and the one thing to do next. */
export function WelcomeStepper(props: Props) {
  return <div className="space-y-4">
    <Steps progress={props.progress} />
    {props.progress.payout ? <Payout payout={props.progress.payout} /> : <Holds progress={props.progress} />}
    <NextAction {...props} />
  </div>;
}
