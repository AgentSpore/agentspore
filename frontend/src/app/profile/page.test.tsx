// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProfilePage from './page';
import { renderToString } from 'react-dom/server';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';

vi.mock('next/navigation', () => ({ usePathname: () => '/profile', useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }) }));
const user = (name: string) => ({ id: name, name, email: `${name}@example.invalid`, avatar_url: null, token_balance: 0, solana_wallet: null, aspore_balance: 0, is_admin: false, created_at: '2026-01-01' });
const responses = new Map<string, Promise<Response>>();
const payoutResponses = new Map<string, Promise<Response>>();
beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } });
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options?: RequestInit) => {
    const authorization = new Headers(options?.headers).get('Authorization') ?? '';
    if (String(_url).endsWith('/payouts')) return payoutResponses.get(authorization) ?? Response.json([]);
    if (!String(_url).endsWith('/auth/me')) return Response.json([]);
    return responses.get(authorization) ?? new Response('[]', { status: 200 });
  }));
});
afterEach(() => { cleanup(); responses.clear(); payoutResponses.clear(); vi.unstubAllGlobals(); });
const mount = () => render(<LocaleProvider initialLocale="en"><ProfilePage /></LocaleProvider>);

it('observes anonymous, signed-in and signed-out sessions without leaking the previous profile', async () => {
  mount();
  expect(screen.getByRole('main').textContent).toContain('Sign In');
  responses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(user('Synthetic Alpha'))));
  await act(async () => { localStorage.setItem('access_token', 'synthetic-session-a'); window.dispatchEvent(new Event('storage')); });
  expect(screen.getByRole('heading', { name: 'Synthetic Alpha' })).toBeTruthy();
  await act(async () => { localStorage.removeItem('access_token'); window.dispatchEvent(new Event('storage')); });
  expect(screen.queryByRole('heading', { name: 'Synthetic Alpha' })).toBeNull();
  expect(screen.getByRole('main').textContent).toContain('Sign In');
});

it('cannot restore a previous profile after its pending request completes', async () => {
  let respond: (response: Response) => void = () => {};
  responses.set('Bearer synthetic-session-a', new Promise(resolve => { respond = resolve; }));
  localStorage.setItem('access_token', 'synthetic-session-a');
  mount();
  responses.set('Bearer synthetic-session-b', Promise.resolve(Response.json(user('Synthetic Beta'))));
  await act(async () => { localStorage.setItem('access_token', 'synthetic-session-b'); window.dispatchEvent(new Event('storage')); });
  await act(async () => { respond(Response.json(user('Synthetic Alpha'))); });
  expect(screen.queryByRole('heading', { name: 'Synthetic Alpha' })).toBeNull();
  expect(screen.getByRole('heading', { name: 'Synthetic Beta' })).toBeTruthy();
});

it('renders anonymous when browser storage is denied', async () => {
  vi.stubGlobal('localStorage', { getItem() { throw new DOMException('Synthetic denial', 'SecurityError'); } });
  await act(async () => { mount(); });
  expect(screen.getByRole('main').textContent).toContain('Sign In');
});

it('ignores delayed payouts belonging to the previous session', async () => {
  let respond: (response: Response) => void = () => {};
  payoutResponses.set('Bearer synthetic-session-a', new Promise(resolve => { respond = resolve; }));
  responses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(user('Synthetic Alpha'))));
  localStorage.setItem('access_token', 'synthetic-session-a');
  await act(async () => { mount(); });
  responses.set('Bearer synthetic-session-b', Promise.resolve(Response.json(user('Synthetic Beta'))));
  await act(async () => { localStorage.setItem('access_token', 'synthetic-session-b'); window.dispatchEvent(new Event('storage')); });
  await act(async () => { respond(Response.json([{ id: 'synthetic-payout', amount: 876543, contribution_points: 1, period_start: '2026-01-01', period_end: '2026-01-31', status: 'pending', tx_signature: null }])); });
  expect(screen.getByRole('heading', { name: 'Synthetic Beta' })).toBeTruthy();
  expect(screen.queryByText('876,543 $ASPORE')).toBeNull();
});

it('keeps the pending server snapshot without reading browser storage', () => {
  const read = vi.fn(() => { throw new Error('SSR must not read storage'); });
  vi.stubGlobal('localStorage', { getItem: read });
  expect(renderToString(<LocaleProvider initialLocale="en"><ProfilePage /></LocaleProvider>)).not.toContain('Sign In');
  expect(read).not.toHaveBeenCalled();
});

function mountWallet(wallet: string | null, locale: 'en' | 'ru', walletResponse = new Response('{}', { status: 503 })) {
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
    if (String(url).endsWith('/auth/me')) return Response.json({ ...user('Synthetic owner'), solana_wallet: wallet });
    if (String(url).endsWith('/users/solana-wallet')) return walletResponse.clone();
    return Response.json([]);
  }));
  localStorage.setItem('access_token', 'synthetic-wallet-session');
  render(<LocaleProvider initialLocale={locale}><ProfilePage /></LocaleProvider>);
}

it.each(['en', 'ru'] as const)('shows a failed disconnect while keeping the connected wallet in %s', async locale => {
  const wallet = '11111111111111111111111111111111';
  mountWallet(wallet, locale);
  fireEvent.click(await screen.findByRole('button', { name: accountMessages[locale].disconnect }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', accountMessages[locale].failedToDisconnect);
  expect(screen.getByText(wallet)).toBeTruthy();
});

it('names the wallet input and submits its existing native form', async () => {
  mountWallet(null, 'en');
  await screen.findByRole('button', { name: accountMessages.en.connect });
  const input = screen.getByRole('textbox', { name: accountMessages.en.solanaWalletAddress });
  const form = input.closest('form');
  expect(form).not.toBeNull();
  if (!form) throw new Error('Wallet form missing');
  fireEvent.change(input, { target: { value: '11111111111111111111111111111111' } });
  fireEvent.submit(form);
  expect(await screen.findByRole('alert')).toBeTruthy();
});

it('renders a schema validation array as invalid address without losing the profile', async () => {
  const detail = [{ type: 'value_error', loc: ['body', 'solana_wallet'],
    msg: 'Value error, Invalid Solana address (base58, 32-44 chars)', input: 'invalid-wallet', ctx: { error: {} } }];
  mountWallet(null, 'en', Response.json({ detail }, { status: 422 }));
  const input = await screen.findByPlaceholderText(accountMessages.en.solanaWalletAddress);
  fireEvent.change(input, { target: { value: 'invalid-wallet' } });
  fireEvent.click(screen.getByRole('button', { name: accountMessages.en.connect }));
  expect(await screen.findByText(accountMessages.en.invalidAddress)).toBeTruthy();
  expect(screen.getByRole('main')).toBeTruthy();
});

it('localizes the wallet ownership conflict in Russian', async () => {
  const detail = 'Solana wallet already connected to another account';
  const translated = 'Кошелёк Solana уже подключён к другому аккаунту.';
  mountWallet(null, 'ru', Response.json({ detail }, { status: 409 }));
  const input = await screen.findByPlaceholderText(accountMessages.ru.solanaWalletAddress);
  fireEvent.change(input, { target: { value: '11111111111111111111111111111111' } });
  fireEvent.click(screen.getByRole('button', { name: accountMessages.ru.connect }));
  await screen.findByText(text => text === detail || text === translated);
  expect(screen.queryByText(detail)).toBeNull();
  expect(screen.getByText(translated)).toBeTruthy();
});
