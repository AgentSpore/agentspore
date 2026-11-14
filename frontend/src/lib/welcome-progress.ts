export const STEP_IDS = ['email', 'wallet', 'application', 'review', 'payout'] as const;
export const HOLD_REASONS = [
  'email_not_confirmed', 'disposable_email', 'wallet_missing', 'wallet_already_used',
  'github_too_new', 'github_age_unknown', 'other_hold',
] as const;
export const NEXT_STEPS = [
  'confirm_email', 'change_email', 'relink_github', 'bind_wallet', 'apply', 'wait_review', 'wait_payout', 'none',
] as const;
const PAYOUT_STATES = ['pending', 'sent', 'confirmed'] as const;
const SIGNATURE_PATTERN = /^[1-9A-HJ-NP-Za-km-z]{64,90}$/;

export type StepId = (typeof STEP_IDS)[number];
export type HoldReason = (typeof HOLD_REASONS)[number];
export type NextStep = (typeof NEXT_STEPS)[number];
export interface WelcomePayout { state: (typeof PAYOUT_STATES)[number]; signature: string | null }
export interface WelcomeProgress {
  steps: { id: StepId; done: boolean }[];
  holds: HoldReason[];
  payout: WelcomePayout | null;
  waitUntil: Date | null;
  nextStep: NextStep;
  githubRelinkPath: string | null;
}

function oneOf<const T extends readonly string[]>(list: T, value: unknown): T[number] | null {
  return list.find(item => item === value) ?? null;
}
function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? Object.fromEntries(Object.entries(value)) : null;
}
function readSteps(value: unknown): WelcomeProgress['steps'] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(item => {
    const step = record(item);
    const id = oneOf(STEP_IDS, step?.id);
    return id && typeof step?.done === 'boolean' ? [{ id, done: step.done }] : [];
  });
}
function readPayout(value: unknown): WelcomePayout | null {
  const payout = record(value);
  const state = oneOf(PAYOUT_STATES, payout?.state);
  if (!state) return null;
  const signature = typeof payout?.signature === 'string' && SIGNATURE_PATTERN.test(payout.signature) ? payout.signature : null;
  return { state, signature };
}
function readRelinkPath(value: unknown): string | null {
  if (!Array.isArray(value)) return null;
  const action = value.map(record).find(item => item?.id === 'github_relink');
  return typeof action?.path === 'string' && action.path.startsWith('/api/') ? action.path : null;
}

/** Older servers send none of these fields: return null so the card keeps its basic form. An unknown hold shows as a manual check, never as an error. */
export function decodeWelcomeProgress(data: Record<string, unknown>): WelcomeProgress | null {
  const steps = readSteps(data.steps);
  if (steps.length === 0) return null;
  const holds = Array.isArray(data.hold_reasons) ? data.hold_reasons.map(hold => oneOf(HOLD_REASONS, hold) ?? 'other_hold') : [];
  const until = typeof data.wait_until === 'string' ? new Date(data.wait_until) : null;
  return {
    steps, holds: [...new Set(holds)], payout: readPayout(data.payout),
    waitUntil: until && !Number.isNaN(until.getTime()) ? until : null,
    nextStep: oneOf(NEXT_STEPS, data.next_step) ?? 'none',
    githubRelinkPath: readRelinkPath(data.actions),
  };
}
