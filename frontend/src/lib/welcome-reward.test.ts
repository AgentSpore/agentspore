import { expect, it } from 'vitest';
import { decodeWelcomeReward, formatRewardAmount } from './welcome-reward';

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
