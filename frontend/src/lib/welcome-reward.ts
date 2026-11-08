import { localeTag, type Locale } from './i18n/locale';

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
  return { state, reason, amount_raw: readAmount(data.amount_raw, state === 'frozen' || state === 'available') };
}
/** Preserve all six raw-unit decimals, including values near the safe integer limit. */
export function formatRewardAmount(raw: number, locale: Locale): string {
  const scale = BigInt(1000000), amount = BigInt(raw);
  const format = new Intl.NumberFormat(localeTag(locale));
  const fraction = (amount % scale).toString().padStart(6, '0').replace(/0+$/, '');
  const separator = format.formatToParts(1.1).find(part => part.type === 'decimal')?.value ?? '.';
  return format.format(amount / scale) + (fraction ? separator + fraction : '');
}
