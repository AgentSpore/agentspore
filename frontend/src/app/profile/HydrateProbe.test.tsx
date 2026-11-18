// @vitest-environment jsdom
import { act, screen, waitFor } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import { hydrateRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import ProfilePage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';

vi.mock('next/navigation', () => ({ usePathname: () => '/profile', useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }) }));
it('shows the email confirmed notice after server render and hydration', async () => {
  const values = new Map<string, string>([['access_token', 'x']]);
  vi.stubGlobal('localStorage', { getItem: (k: string) => values.get(k) ?? null, setItem: (k: string, v: string) => { values.set(k, v); }, removeItem: (k: string) => { values.delete(k); } });
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
    const p = String(url);
    if (p.endsWith('/auth/me')) return Response.json({ id: 'u1', name: 'Synthetic owner', email: 'o@example.invalid', avatar_url: null, token_balance: 0, solana_wallet: null, is_admin: false, created_at: '2026-01-01' });
    if (p.endsWith('/rewards/welcome')) return Response.json({ state: 'disabled', reason: 'program_disabled', amount_raw: null, decimals: 6, asset: 'ASPORE', next_action: null, hold_reasons: [], payout: null, wait_until: null, actions: [] });
    if (p.endsWith('/users/me/aspore')) return Response.json({ aspore_balance: 0 });
    return Response.json([]);
  }));
  window.history.replaceState(null, '', '/profile?email=verified');
  const tree = <LocaleProvider initialLocale="en"><ProfilePage /></LocaleProvider>;
  const container = document.createElement('div');
  document.body.appendChild(container);
  container.innerHTML = renderToString(tree);
  await act(async () => { hydrateRoot(container, tree); });
  await screen.findByRole('heading', { name: 'Synthetic owner' });
  await waitFor(() => expect(screen.getByText(accountMessages.en.profileEmailConfirmed)).toBeTruthy());
});
