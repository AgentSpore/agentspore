import { expect, it } from 'vitest';
import { decodeWelcomeReward, formatRewardAmount, welcomeWalletFlags } from './welcome-reward';

const disabled = { state: 'disabled', reason: 'program_disabled', amount_raw: null, decimals: 6, asset: 'ASPORE', next_action: 'await_program' };
it('keeps conditional applications distinct from funded grants', () => {
  expect(decodeWelcomeReward(disabled).amount_raw).toBeNull();
  expect(decodeWelcomeReward({ ...disabled, state: 'application', reason: 'funding_unavailable', next_action: 'await_funding' }).amount_raw).toBeNull();
  expect(decodeWelcomeReward({ ...disabled, state: 'frozen', reason: 'email_required', amount_raw: 10000000000, next_action: 'verify_email' }).amount_raw).toBe(10000000000);
});
it.each([
  { state: 'unknown' }, { reason: 'private risk detail' }, { amount_raw: 10000000000 },
  { state: 'available', reason: 'available' }, { decimals: 9 }, { asset: 'SOL' },
  { state: 'frozen', amount_raw: Number.MAX_SAFE_INTEGER + 1 },
  { state: 'frozen', amount_raw: -1 }, { state: 'frozen', amount_raw: 0.5 },
  { state: 'disabled', reason: 'available' }, { next_action: 'send_wallet' },
])('rejects malformed or misleading reward payloads %j', delta => {
  expect(() => decodeWelcomeReward({ ...disabled, ...delta })).toThrow();
});
it('formats exact raw units without rounding the smallest unit', () => {
  expect(formatRewardAmount(10000000000, 'en')).toBe('10,000');
  expect(formatRewardAmount(Number.MAX_SAFE_INTEGER, 'en')).toBe('9,007,199,254.740991');
  expect(formatRewardAmount(1, 'ru')).toBe('0,000001');
});

const stepIds = ['email', 'wallet', 'application', 'review', 'payout'];
const withSteps = (delta: object, nextStep: string, extra: object = {}) => decodeWelcomeReward({
  ...disabled, ...delta, steps: stepIds.map(id => ({ id, done: false })), hold_reasons: [], payout: null, wait_until: null, next_step: nextStep, actions: [], ...extra,
});
const live = { state: 'application', reason: 'risk_review', next_action: 'await_review' };
const owns = (data: ReturnType<typeof decodeWelcomeReward>) => welcomeWalletFlags({ kind: 'success', data }).ownsWallet;
it.each([
  ['disabled', { state: 'disabled', reason: 'program_disabled' }],
  ['denied', { state: 'denied', reason: 'denied', next_action: 'none' }],
  ['not_eligible', { state: 'not_applied', reason: 'not_eligible', next_action: 'none' }],
])('leaves the wallet field to the profile when the stepper is inert (%s)', (_name, delta) => {
  const data = withSteps(delta, 'none');
  expect(data.progress?.nextStep).toBe('none');
  expect(welcomeWalletFlags({ kind: 'success', data }).ownsWallet).toBe(false);
});
it('owns the wallet field while the stepper is live and the wallet step is undone', () => {
  const data = withSteps({ state: 'application', reason: 'risk_review', next_action: 'await_review' }, 'change_email');
  expect(welcomeWalletFlags({ kind: 'success', data }).ownsWallet).toBe(true);
});
it('leaves the wallet field to the profile once a payout exists, even if the wallet was disconnected', () => {
  expect(owns(withSteps(live, 'wait_payout', { payout: { state: 'pending', signature: null } }))).toBe(false);
});
it('owns the wallet field for relink_github only when the relink link is offered', () => {
  expect(owns(withSteps(live, 'relink_github'))).toBe(false);
  expect(owns(withSteps(live, 'relink_github', { actions: [{ id: 'github_relink', path: '/api/v1/relink' }] }))).toBe(true);
});
