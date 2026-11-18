// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProfilePage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';

vi.mock('next/navigation', () => ({ usePathname: () => '/profile', useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }) }));
const WALLET = '4Nd1mYQzvgvbUQvTzxWCvJGZaAF5bYhQ7pTvVxkXvQfM';
const owner = { id: 'u1', name: 'Synthetic owner', email: 'owner@example.invalid', avatar_url: null, token_balance: 0, solana_wallet: WALLET, is_admin: false, created_at: '2026-01-01' };
const steps = ['email', 'wallet', 'application', 'review', 'payout'].map(id => ({ id, done: ['email', 'wallet', 'application'].includes(id) }));
const pending = { state: 'application', reason: 'risk_review', amount_raw: null, decimals: 6, asset: 'ASPORE', next_action: 'await_review',
  hold_reasons: [], payout: null, wait_until: null, actions: [], steps, next_step: 'wait_review' };
const MACHINE_VALUE = /^(>_|[A-Z?]{1,2}|[1-9A-HJ-NP-Za-km-z]{32,44}|[\d\s.,$A-Z]+)$/;

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } });
  localStorage.setItem('access_token', 'synthetic-session');
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url);
    if (path.endsWith('/rewards/welcome')) return Response.json(pending);
    if (path.endsWith('/auth/me')) return Response.json(owner);
    if (path.endsWith('/users/me/aspore')) return Response.json({ aspore_balance: 0 });
    return Response.json([]);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(['en', 'ru'] as const)('profile has no terminal title bar and keeps mono for machine values only in %s', async locale => {
  const { container } = render(<LocaleProvider initialLocale={locale}><ProfilePage /></LocaleProvider>);
  await screen.findByText(WALLET);
  expect(document.body.textContent).not.toMatch(/(profile|reset)@agentspore/);
  const mono = [...container.querySelectorAll('main .font-mono')].filter(el => !(el instanceof HTMLInputElement));
  const prose = mono.map(el => el.textContent?.trim() ?? '').filter(text => text && !MACHINE_VALUE.test(text));
  expect(prose).toEqual([]);
});

it.each([['en', 'Under review'], ['ru', 'На рассмотрении']] as const)('pending application badge says what happens next in %s', async (locale, label) => {
  render(<LocaleProvider initialLocale={locale}><ProfilePage /></LocaleProvider>);
  expect(await screen.findByText(label)).toBeTruthy();
  expect(screen.queryByText(/Application pending|Заявка рассматривается/)).toBeNull();
});
