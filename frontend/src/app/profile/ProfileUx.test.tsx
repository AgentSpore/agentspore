// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProfilePage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';
import { welcomeRewardMessages } from '@/lib/i18n/welcomeReward';

vi.mock('next/navigation', () => ({ usePathname: () => '/profile', useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }) }));
const WALLET = '4Nd1mYQzvgvbUQvTzxWCvJGZaAF5bYhQ7pTvVxkXvQfM';
const owner = (wallet: string | null) => ({ id: 'u1', name: 'Synthetic owner', email: 'owner@example.invalid', avatar_url: null, token_balance: 0, solana_wallet: wallet, is_admin: false, created_at: '2026-01-01' });
const steps = (done: string[]) => ['email', 'wallet', 'application', 'review', 'payout'].map(id => ({ id, done: done.includes(id) }));
const welcome = (over: object) => ({ state: 'application', reason: 'risk_review', amount_raw: null, decimals: 6, asset: 'ASPORE', next_action: 'await_review',
  hold_reasons: [], payout: null, wait_until: null, actions: [], ...over });
const disabled = () => welcome({ state: 'disabled', reason: 'program_disabled' });

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } });
  localStorage.setItem('access_token', 'synthetic-session');
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, '', '/'); });

function stubApi(routes: { me: () => object; welcome: () => object; onPatch?: () => void }) {
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path.endsWith('/users/solana-wallet') && init?.method === 'PATCH') { routes.onPatch?.(); return new Response('{}', { status: 200 }); }
    if (path.endsWith('/rewards/welcome')) return Response.json(routes.welcome());
    if (path.endsWith('/auth/me')) return Response.json(routes.me());
    if (path.endsWith('/users/me/aspore')) return Response.json({ aspore_balance: 0 });
    return Response.json([]);
  }));
}
const mount = (locale: 'en' | 'ru' = 'en') => render(<LocaleProvider initialLocale={locale}><ProfilePage /></LocaleProvider>);

it('labels the saved wallet in readable English', async () => {
  stubApi({ me: () => owner(WALLET), welcome: disabled });
  mount();
  expect(await screen.findByText('Connected wallet')).toBeTruthy();
});

it('does not say there are no payouts while the welcome card shows a sent payout', async () => {
  stubApi({ me: () => owner(WALLET), welcome: () => welcome({ state: 'available', reason: 'available', amount_raw: 1000000,
    steps: steps(['email', 'wallet', 'application', 'review', 'payout']), payout: { state: 'confirmed', signature: '1'.repeat(88) }, next_step: 'none' }) });
  mount();
  expect(await screen.findByText(welcomeRewardMessages.en.payoutConfirmed)).toBeTruthy();
  expect(screen.queryByText(accountMessages.en.noPayouts)).toBeNull();
  expect(screen.getByText(accountMessages.en.noPayoutsWelcome)).toBeTruthy();
});

it('updates the lower wallet section at once after the welcome card saves the wallet', async () => {
  let saved = false;
  stubApi({
    me: () => owner(saved ? WALLET : null),
    welcome: () => welcome({ hold_reasons: saved ? [] : ['wallet_missing'], steps: steps(saved ? ['email', 'wallet', 'application'] : ['email', 'application']), next_step: saved ? 'wait_review' : 'bind_wallet' }),
    onPatch: () => { saved = true; },
  });
  mount();
  fireEvent.change(await screen.findByLabelText(welcomeRewardMessages.en.walletLabel), { target: { value: WALLET } });
  fireEvent.click(screen.getByRole('button', { name: welcomeRewardMessages.en.walletSave }));
  expect(await screen.findByText(WALLET)).toBeTruthy();
  expect(screen.queryByPlaceholderText(accountMessages.en.solanaWalletAddress)).toBeNull();
  expect(screen.getByRole('heading', { name: 'Synthetic owner' })).toBeTruthy();
});

it('tells the owner the review needs nothing from them and where the result appears', async () => {
  stubApi({ me: () => owner(WALLET), welcome: () => welcome({ steps: steps(['email', 'wallet', 'application']), next_step: 'wait_review' }) });
  mount();
  expect(await screen.findByText(welcomeRewardMessages.en.next_wait_review)).toBeTruthy();
  expect(welcomeRewardMessages.en.next_wait_review).toMatch(/result will appear/i);
  expect(welcomeRewardMessages.ru.next_wait_review).toMatch(/результат/);
});

it.each(['en', 'ru'] as const)('shows a one-time notice after the email link and removes the marker (%s)', async locale => {
  window.history.replaceState(null, '', '/profile?email=verified');
  stubApi({ me: () => owner(WALLET), welcome: disabled });
  mount(locale);
  expect(await screen.findByText(accountMessages[locale].profileEmailConfirmed)).toBeTruthy();
  await waitFor(() => expect(window.location.search).toBe(''));
});
