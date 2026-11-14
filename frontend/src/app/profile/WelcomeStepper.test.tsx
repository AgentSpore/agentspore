// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { emailChangeMessages } from '@/lib/i18n/emailChange';
import { welcomeRewardMessages as m } from '@/lib/i18n/welcomeReward';
import { HOLD_REASONS } from '@/lib/welcome-progress';
import { decodeWelcomeReward } from '@/lib/welcome-reward';
import { WelcomeReward } from './WelcomeReward';
import type { ProfileRead } from './useProfileRead';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const SIGNATURE = '5'.repeat(88);
const STEP_IDS = ['email', 'wallet', 'application', 'review', 'payout'];
const wire = (over: Record<string, unknown> = {}, done = 0) => ({
  state: 'application', reason: 'risk_review', amount_raw: null, decimals: 6, asset: 'ASPORE', next_action: 'await_review',
  steps: STEP_IDS.map((id, index) => ({ id, done: index < done })), hold_reasons: [], payout: null, wait_until: null,
  next_step: 'wait_review', actions: [], ...over,
});
function mount(payload: Record<string, unknown>, locale: 'en' | 'ru' = 'en', hasPassword?: boolean) {
  const read: ProfileRead<ReturnType<typeof decodeWelcomeReward>> = {
    state: { kind: 'success', data: decodeWelcomeReward(payload) }, retry: vi.fn(), replace: vi.fn(),
  };
  render(<LocaleProvider initialLocale={locale}><WelcomeReward read={read} token="synthetic-owner" hasPassword={hasPassword} /></LocaleProvider>);
  return read;
}

it('marks the first unfinished step as current and lists all five', () => {
  mount(wire({}, 2));
  const steps = within(screen.getByRole('list', { name: m.en.stepsLabel })).getAllByRole('listitem');
  expect(steps).toHaveLength(5);
  expect(steps.map(step => step.getAttribute('aria-current'))).toEqual([null, null, 'step', null, null]);
});

it.each([
  ['confirm_email', () => screen.getByRole('button', { name: m.en.send })],
  ['change_email', () => screen.getByLabelText(emailChangeMessages.en.newEmail)],
  ['bind_wallet', () => screen.getByLabelText(m.en.walletLabel)],
  ['apply', () => screen.getByRole('button', { name: m.en.apply })],
  ['wait_review', () => screen.getByText(m.en.next_wait_review)],
  ['wait_payout', () => screen.getByText(m.en.next_wait_payout)],
] as const)('offers the %s action inline', (next, find) => {
  mount(wire({ next_step: next }));
  expect(find()).toBeTruthy();
});

it('relinks GitHub by navigation, not fetch', () => {
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  mount(wire({ next_step: 'relink_github', actions: [{ id: 'github_relink', method: 'GET', path: '/api/v1/oauth/github' }] }));
  expect(screen.getByRole('link', { name: m.en.relink }).getAttribute('href')).toMatch(/\/api\/v1\/oauth\/github$/);
  expect(fetchMock).not.toHaveBeenCalled();
});

it.each(['en', 'ru'] as const)('words every hold reason for people in %s', locale => {
  for (const hold of HOLD_REASONS) {
    cleanup();
    mount(wire({ hold_reasons: [hold] }), locale);
    const text = m[locale][`hold_${hold}`];
    expect(screen.getByText(text)).toBeTruthy();
    expect(text).not.toMatch(/hold|_/i);
  }
});

it('turns an unknown hold code into a manual check', () => {
  mount(wire({ hold_reasons: ['future_code'] }));
  expect(screen.getByText(m.en.hold_other_hold)).toBeTruthy();
});

it('shows wait_until as a UTC date next to the GitHub age hold', () => {
  mount(wire({ hold_reasons: ['github_too_new'], wait_until: '2026-12-01T23:30:00Z' }));
  expect(screen.getByText(/December 1, 2026/)).toBeTruthy();
});

it.each([
  ['pending', null, m.en.payoutPending, false], ['sent', SIGNATURE, m.en.payoutSent, false], ['confirmed', SIGNATURE, m.en.payoutConfirmed, true],
] as const)('payout %s: transaction link only when confirmed', (state, signature, text, linked) => {
  mount(wire({ state: 'available', reason: 'available', amount_raw: 5000000, next_step: state === 'confirmed' ? 'none' : 'wait_payout', payout: { state, signature } }, 4));
  expect(screen.getByText(text)).toBeTruthy();
  expect(screen.queryByText(m.en.available)).toBeNull();
  expect(screen.queryByRole('link', { name: m.en.viewTx }) !== null).toBe(linked);
  if (linked) expect(screen.getByRole('link', { name: m.en.viewTx }).getAttribute('href')).toBe(`https://solscan.io/tx/${SIGNATURE}`);
});

it('ignores a malformed signature instead of linking it', () => {
  mount(wire({ state: 'available', reason: 'available', amount_raw: 5000000, payout: { state: 'confirmed', signature: '"><script>' } }));
  expect(screen.queryByRole('link', { name: m.en.viewTx })).toBeNull();
});

it('binds the wallet inline and refreshes the card', async () => {
  const fetchMock = vi.fn(async () => Response.json({ status: 'connected' }));
  vi.stubGlobal('fetch', fetchMock);
  const read = mount(wire({ next_step: 'bind_wallet', hold_reasons: ['wallet_missing'] }));
  fireEvent.change(screen.getByLabelText(m.en.walletLabel), { target: { value: 'So11111111111111111111111111111111111111112' } });
  fireEvent.click(screen.getByRole('button', { name: m.en.walletSave }));
  await vi.waitFor(() => expect(read.retry).toHaveBeenCalledOnce());
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/users/solana-wallet'), expect.objectContaining({ method: 'PATCH' }));
});

it.each([[409, 'walletTaken'], [503, 'walletFailed']] as const)('shows a plain wallet error for %s', async (status, key) => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'internal' }, { status })));
  mount(wire({ next_step: 'bind_wallet' }));
  fireEvent.change(screen.getByLabelText(m.en.walletLabel), { target: { value: 'So11111111111111111111111111111111111111112' } });
  fireEvent.click(screen.getByRole('button', { name: m.en.walletSave }));
  expect((await screen.findByRole('alert')).textContent).toBe(m.en[key]);
});

it('rejects a malformed wallet address without a request', () => {
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  mount(wire({ next_step: 'bind_wallet' }));
  fireEvent.change(screen.getByLabelText(m.en.walletLabel), { target: { value: 'not-an-address' } });
  fireEvent.click(screen.getByRole('button', { name: m.en.walletSave }));
  expect(screen.getByRole('alert').textContent).toBe(m.en.walletInvalid);
  expect(fetchMock).not.toHaveBeenCalled();
});

it('keeps the basic card for a server that sends no progress fields', () => {
  mount({ state: 'disabled', reason: 'program_disabled', amount_raw: null, decimals: 6, asset: 'ASPORE', next_action: 'await_program' });
  expect(screen.queryByRole('list')).toBeNull();
});

it.each([
  ['denied', 'denied', 'state_denied'], ['disabled', 'program_disabled', 'disabled'], ['not_applied', 'not_eligible', 'state_not_applied'],
] as const)('shows the final state, not a current step, when %s has nothing to do', (state, reason, label) => {
  mount(wire({ state, reason, next_step: 'none', actions: [], hold_reasons: [], next_action: 'none' }, 1));
  expect(screen.getByText(m.en[label])).toBeTruthy();
  const steps = within(screen.getByRole('list', { name: m.en.stepsLabel })).getAllByRole('listitem');
  expect(steps.every(step => step.getAttribute('aria-current') === null)).toBe(true);
  expect(screen.queryByText(m.en.stepCurrent)).toBeNull();
});

const fillWallet = () => {
  fireEvent.change(screen.getByLabelText(m.en.walletLabel), { target: { value: 'So11111111111111111111111111111111111111112' } });
  fireEvent.click(screen.getByRole('button', { name: m.en.walletSave }));
};

it('announces the saved wallet and moves focus to the card heading', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({}, { status: 200 })));
  const read = mount(wire({ next_step: 'bind_wallet' }));
  fillWallet();
  expect((await screen.findByText(m.en.walletSaved)).getAttribute('aria-live')).toBe('polite');
  expect(document.activeElement).toBe(screen.getByRole('heading', { name: m.en.title }));
  expect(read.retry).toHaveBeenCalledOnce();
});

it('announces a sent application and keeps focus on the card heading', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(wire({ next_step: 'wait_review' }), { status: 200 })));
  mount(wire({ next_step: 'apply' }));
  fireEvent.click(screen.getByRole('button', { name: m.en.apply }));
  expect((await screen.findByText(m.en.applied)).getAttribute('aria-live')).toBe('polite');
  expect(document.activeElement).toBe(screen.getByRole('heading', { name: m.en.title }));
});

it.each([[true, true], [false, null]] as const)('passes has_password=%s to the email form', (has, field) => {
  mount(wire({ next_step: 'change_email' }), 'en', has);
  const input = screen.queryByLabelText(emailChangeMessages.en.password) as HTMLInputElement | null;
  expect(field === true ? input?.required : input).toBe(field);
});

it('reads has_password from the account only when the server sends it', async () => {
  const { decodeProfile } = await import('./useProfileRead');
  const row = { id: 'u', name: 'N', email: 'e@example.invalid', avatar_url: null, solana_wallet: null, token_balance: 0, created_at: '2026-01-01' };
  expect(decodeProfile({ ...row, has_password: false }).has_password).toBe(false);
  expect('has_password' in decodeProfile(row)).toBe(false);
});

it('links the expired-session wallet notice to sign-in', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({}, { status: 401 })));
  mount(wire({ next_step: 'bind_wallet' }));
  fillWallet();
  expect((await screen.findByRole('link', { name: m.en.signIn })).getAttribute('href')).toBe('/login?next=/profile');
});

it.each(['confirm_email', 'change_email', 'relink_github', 'bind_wallet', 'apply'])('does not claim "no action needed" while %s is the next step', next => {
  mount(wire({ next_step: next }));
  expect(screen.queryByText(m.en.risk_review)).toBeNull();
});

it('keeps the checks-in-progress text while the only thing to do is wait', () => {
  mount(wire({ next_step: 'wait_review' }));
  expect(screen.getByText(m.en.risk_review)).toBeTruthy();
});

it.each([
  ['approved', { next_step: 'wait_payout' }, 4, 'badge_approved'],
  ['payout pending', { next_step: 'wait_payout', payout: { state: 'pending' } }, 4, 'badge_approved'],
  ['payout sent', { next_step: 'wait_payout', payout: { state: 'sent', signature: SIGNATURE } }, 4, 'badge_sending'],
  ['payout confirmed', { next_step: 'none', payout: { state: 'confirmed', signature: SIGNATURE } }, 5, 'badge_sent'],
] as const)('badge follows the real state: %s', (_name, over, done, key) => {
  mount(wire(over, done));
  expect(screen.getByText(m.en[key])).toBeTruthy();
  expect(screen.queryByText(m.en.application)).toBeNull();
  expect(screen.queryByText(m.en.risk_review)).toBeNull();
});

it.each(['en', 'ru'] as const)('words every badge in %s', locale => {
  for (const key of ['badge_approved', 'badge_sending', 'badge_sent'] as const) expect(m[locale][key]).toBeTruthy();
});

it('keeps the application badge while waiting for review', () => {
  mount(wire({ next_step: 'bind_wallet' }, 1));
  expect(screen.getByText(m.en.application)).toBeTruthy();
});

it('gives the wallet field a format hint without internal limits', () => {
  mount(wire({ next_step: 'bind_wallet' }));
  expect(screen.getByText(m.en.walletHint).textContent).toMatch(/32.44/);
  expect(screen.getByLabelText(m.en.walletLabel).getAttribute('placeholder')).toBe(m.en.walletPlaceholder);
});

it('puts the current-step marker on the email step while the email must be changed', () => {
  const steps = STEP_IDS.map(id => ({ id, done: id === 'wallet' || id === 'application' }));
  mount(wire({ steps, hold_reasons: ['disposable_email'], next_step: 'change_email' }));
  const items = within(screen.getByRole('list', { name: m.en.stepsLabel })).getAllByRole('listitem');
  expect(items.map(step => step.getAttribute('aria-current'))).toEqual(['step', null, null, null, null]);
});

it.each(['confirm_email', 'change_email', 'bind_wallet', 'apply', 'relink_github'])('shows a visible focus ring on every %s control', next => {
  mount(wire({ next_step: next, actions: [{ id: 'github_relink', method: 'GET', path: '/api/v1/oauth/github' }] }));
  const controls = [...screen.queryAllByRole('button'), ...screen.queryAllByRole('textbox'), ...screen.queryAllByRole('link'), ...document.querySelectorAll('input')];
  expect(controls.length).toBeGreaterThan(0);
  for (const control of controls) expect(control.className).toContain('focus-visible:ring-2');
});

it('uses neutral Russian step statuses that agree with every step name', () => {
  expect([m.ru.stepDone, m.ru.stepLater, m.ru.stepCurrent]).toEqual(['выполнено', 'ещё не начато', 'текущий шаг']);
});

it.each(['en', 'ru'] as const)('tells to change the email before adding the wallet in %s', locale => {
  mount(wire({ hold_reasons: ['disposable_email', 'wallet_missing'], next_step: 'change_email' }), locale);
  expect(screen.getByText(m[locale].hold_wallet_after_email)).toBeTruthy();
  expect(screen.queryByText(m[locale].hold_wallet_missing)).toBeNull();
});

it('keeps the plain wallet hold when the email is fine', () => {
  mount(wire({ hold_reasons: ['wallet_missing'], next_step: 'bind_wallet' }));
  expect(screen.getByText(m.en.hold_wallet_missing)).toBeTruthy();
});

it('says "Sending payout" for the sending badge', () => {
  expect(m.en.badge_sending).toBe('Sending payout');
});
