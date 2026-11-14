// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { claimReturnPath, consumeClaimReturn, decodeClaim, decodeClaimant, rememberClaimReturn } from './agent-claim';
const path = '/agents/11111111-1111-1111-1111-111111111111/claim';
afterEach(() => { sessionStorage.clear(); vi.useRealTimers(); });
it('requires verified-account projection and exact agent/user bindings', () => {
  expect(() => decodeClaimant({ id: 'u', name: 'User' })).toThrow();
  const row = { claim_id: 'c', agent_id: 'a', user_id: 'u', agent_name: 'Agent', user_name: 'User', status: 'pending', expires_at: '2026-10-27T12:00:00Z' };
  expect(decodeClaim(row, 'a', 'u').status).toBe('pending');
  for (const change of [{ user_id: 'other' }, { agent_id: 'other' }, { status: 'invented' }, { expires_at: 'bad' }]) expect(() => decodeClaim({ ...row, ...change }, 'a', 'u')).toThrow();
});
it('allows only public UUID claim routes and consumes continuation once within ten minutes', () => {
  for (const unsafe of ['//evil.test', '/agents/../profile/claim', path + '?token=x', path + '#x', '/profile', 'https://evil.test' + path]) expect(claimReturnPath(unsafe)).toBeNull();
  rememberClaimReturn(path); expect(consumeClaimReturn()).toBe(path); expect(consumeClaimReturn()).toBeNull();
  vi.useFakeTimers(); rememberClaimReturn(path); vi.advanceTimersByTime(600001); expect(consumeClaimReturn()).toBeNull();
  rememberClaimReturn(path); rememberClaimReturn('/profile'); expect(consumeClaimReturn()).toBeNull();
});
it('also returns to the exact email-change confirmation page, nothing looser', () => {
  const confirm = '/confirm-email-change';
  for (const unsafe of ['//evil.test', confirm + '/x', confirm + '?next=//evil.test', confirm + '#token=x', 'https://evil.test' + confirm, '/confirm-email-changes']) expect(claimReturnPath(unsafe)).toBeNull();
  rememberClaimReturn(confirm); expect(consumeClaimReturn()).toBe(confirm); expect(consumeClaimReturn()).toBeNull();
});
