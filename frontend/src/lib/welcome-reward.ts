import { localeTag, type Locale } from './i18n/locale';
import type { welcomeRewardMessages } from './i18n/welcomeReward';
import { decodeWelcomeProgress, type WelcomeProgress } from './welcome-progress';

const REASONS = {
  disabled: ['program_disabled'], not_applied: ['not_applied', 'not_eligible'],
  application: ['risk_review', 'funding_unavailable'],
  frozen: ['program_disabled', 'risk_review', 'email_required', 'reserved'],
  available: ['available'], denied: ['denied'],
} as const;
const ACTIONS = ['await_program', 'await_review', 'await_funding', 'verify_email', 'none'];
type RewardState = keyof typeof REASONS;
export interface WelcomeRewardStatus {
  state: RewardState;
  reason: (typeof REASONS)[RewardState][number];
  amount_raw: number | null;
  progress?: WelcomeProgress;
}
function isState(value: unknown): value is RewardState {
  return typeof value === 'string' && Object.hasOwn(REASONS, value);
}
function isReason(value: unknown): value is WelcomeRewardStatus['reason'] {
  return typeof value === 'string' && Object.values(REASONS).some(reasons => reasons.some(reason => reason === value));
}
function readEnvelope(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error('Invalid welcome reward response');
  return Object.fromEntries(Object.entries(value));
}
function readAmount(value: unknown, funded: boolean): number | null {
  if (!funded && value === null) return null;
  if (funded && typeof value === 'number' && Number.isSafeInteger(value) && value > 0) return value;
  throw new Error('Invalid welcome reward amount');
}
function validateAsset(value: Record<string, unknown>) {
  if (value.decimals !== 6 || value.asset !== 'ASPORE'
    || typeof value.next_action !== 'string' || !ACTIONS.includes(value.next_action)) {
    throw new Error('Invalid welcome reward response');
  }
}
/** Reject unknown states and numbers that cannot represent exact financial units. */
export function decodeWelcomeReward(value: unknown): WelcomeRewardStatus {
  const data = readEnvelope(value);
  const { state, reason } = data;
  if (!isState(state) || !isReason(reason) || !REASONS[state].some(allowed => allowed === reason)) {
    throw new Error('Invalid welcome reward response');
  }
  validateAsset(data);
  const progress = decodeWelcomeProgress(data);
  return { state, reason, amount_raw: readAmount(data.amount_raw, state === 'frozen' || state === 'available'), ...(progress && { progress }) };
}
/** Preserve all six raw-unit decimals, including values near the safe integer limit. */
export function formatRewardAmount(raw: number, locale: Locale): string {
  const scale = BigInt(1000000), amount = BigInt(raw);
  const format = new Intl.NumberFormat(localeTag(locale));
  const fraction = (amount % scale).toString().padStart(6, '0').replace(/0+$/, '');
  const separator = format.formatToParts(1.1).find(part => part.type === 'decimal')?.value ?? '.';
  return format.format(amount / scale) + (fraction ? separator + fraction : '');
}

const ACTION_STEPS = ['confirm_email', 'change_email', 'relink_github', 'bind_wallet', 'apply'];
const PAYOUT_BADGES = { pending: 'badge_approved', sent: 'badge_sending', confirmed: 'badge_sent' } as const;

/** Message key for the status badge; follows payout and approval, not only the coarse state. */
export function rewardBadgeKey(data: WelcomeRewardStatus): keyof (typeof welcomeRewardMessages)['en'] {
  const progress = data.progress;
  if (progress?.payout) return PAYOUT_BADGES[progress.payout.state];
  if (progress?.nextStep === 'wait_payout' && progress.steps.some(step => step.id === 'review' && step.done)) return 'badge_approved';
  return STATE_BADGES[data.state];
}
const STATE_BADGES = {
  disabled: 'disabled', not_applied: 'state_not_applied', application: 'application',
  frozen: 'frozen', available: 'state_available', denied: 'state_denied',
} as const;

/** The reason line says "no action needed", so it is hidden whenever an action or a payout is shown. */
export function showsRewardReason(data: WelcomeRewardStatus): boolean {
  const progress = data.progress;
  if (!progress) return true;
  return !progress.payout && !(data.reason === 'risk_review' && progress.nextStep !== 'wait_review')
    && !ACTION_STEPS.includes(progress.nextStep);
}

/** Which wallet notes to show, and whether the welcome card currently owns the wallet field. */
export function welcomeWalletFlags(state: { kind: string; data?: WelcomeRewardStatus }) {
  const data = state.kind === 'success' ? state.data : undefined;
  const active = !!data && data.state !== 'disabled';
  return {
    ownsWallet: data?.progress?.nextStep === 'bind_wallet',
    walletNote: active ? 'walletNoteWelcomeActive' : 'connectYourSolanaWalletToReceiveMonthlyASPORE',
    earnNote: active ? 'earnNoteWelcomeActive' : '5MonthlyPayoutsDistributeASPOREProportionalToYour',
  } as const;
}

/** The legacy wallet section shows for a loaded user unless the welcome card owns the wallet field. */
export function showsWalletSection<T>(user: T | null, ownsWallet: boolean): user is T {
  return user !== null && !ownsWallet;
}
