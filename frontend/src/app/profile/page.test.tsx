// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProfilePage from './page';
import { renderToString } from 'react-dom/server';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';
import { sharedMessages } from '@/lib/i18n/shared';
import type { TokenPayout } from '@/lib/api';

vi.mock('next/navigation', () => ({ usePathname: () => '/profile', useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }) }));
const user = (name: string) => ({ id: name, name, email: `${name}@example.invalid`, avatar_url: null, token_balance: 0, solana_wallet: null, is_admin: false, created_at: '2026-01-01' });
const responses = new Map<string, Promise<Response>>();
const payoutResponses = new Map<string, Promise<Response>>();
const balanceResponses = new Map<string, Promise<Response>>();
beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } });
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options?: RequestInit) => {
    const authorization = new Headers(options?.headers).get('Authorization') ?? '';
    if (String(_url).endsWith('/payouts')) return payoutResponses.get(authorization) ?? Response.json([]);
    if (String(_url).endsWith('/users/me/aspore')) return balanceResponses.get(authorization) ?? Response.json({ aspore_balance: 0 });
    if (!String(_url).endsWith('/auth/me')) return Response.json([]);
    return responses.get(authorization) ?? new Response('[]', { status: 200 });
  }));
});
afterEach(() => { cleanup(); responses.clear(); payoutResponses.clear(); balanceResponses.clear(); vi.unstubAllGlobals(); });
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
    if (String(url).endsWith('/users/me/aspore')) return Response.json({ aspore_balance: 0 });
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

const readPayouts = (['pending', 'sent', 'confirmed', 'failed'] as const).map((status, index) => ({
  id: `00000000-0000-4000-8000-00000000000${index}`,
  amount: 1111 + index,
  contribution_points: 10,
  pool_total: 100000,
  period_start: '2026-01-01',
  period_end: '2026-01-31',
  tx_signature: null,
  status,
  created_at: '2026-02-01T00:00:00Z',
  sent_at: null,
  confirmed_at: null,
})) satisfies TokenPayout[];

it('announces the held profile read instead of leaving main blank', async () => {
  let respond: (response: Response) => void = () => {};
  responses.set('Bearer synthetic-session-a', new Promise(resolve => { respond = resolve; }));
  localStorage.setItem('access_token', 'synthetic-session-a');
  await act(async () => { mount(); });
  const main = within(screen.getByRole('main'));
  expect(main.getByRole('status').textContent).toMatch(/loading.*profile/i);
  expect(main.queryByRole('link', { name: accountMessages.en.signIn })).toBeNull();
  await act(async () => { respond(Response.json(user('Synthetic Alpha'))); });
  expect(main.getByRole('heading', { name: 'Synthetic Alpha' })).toBeTruthy();
  expect(main.queryByText(/loading.*profile/i)).toBeNull();
});

it.each(['HTTP 503', 'network'])('recovers a failed profile read through retry after %s', async failure => {
  localStorage.setItem('access_token', 'synthetic-session-a');
  await act(async () => {
    responses.set('Bearer synthetic-session-a', Promise.resolve().then(() => {
      if (failure === 'network') throw new TypeError('Synthetic network failure');
      return new Response('{}', { status: 503 });
    }));
    mount();
  });
  const main = within(screen.getByRole('main'));
  expect(main.queryByRole('link', { name: accountMessages.en.signIn })).toBeNull();
  expect(main.getByRole('alert').textContent).toContain(sharedMessages.en.couldNotLoadData);
  expect(localStorage.getItem('access_token')).toBe('synthetic-session-a');
  responses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(user('Synthetic Alpha'))));
  await act(async () => { fireEvent.click(main.getByRole('button', { name: sharedMessages.en.retry })); });
  expect(main.getByRole('heading', { name: 'Synthetic Alpha' })).toBeTruthy();
  expect(main.queryByRole('alert')).toBeNull();
});

it('distinguishes a held payout read from a successful empty history', async () => {
  let respond: (response: Response) => void = () => {};
  responses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(user('Synthetic Alpha'))));
  payoutResponses.set('Bearer synthetic-session-a', new Promise(resolve => { respond = resolve; }));
  localStorage.setItem('access_token', 'synthetic-session-a');
  await act(async () => { mount(); });
  const main = within(screen.getByRole('main'));
  expect(main.getByRole('heading', { name: accountMessages.en.catPayoutsLog })).toBeTruthy();
  expect(main.getByRole('status').textContent).toMatch(/loading.*payout/i);
  expect(main.queryByText(/no payouts/i)).toBeNull();
  await act(async () => { respond(Response.json([])); });
  expect(main.getByRole('heading', { name: 'Synthetic Alpha' })).toBeTruthy();
  expect(main.getByText(/no payouts/i)).toBeTruthy();
  expect(main.queryByText(/loading.*payout/i)).toBeNull();
  expect(main.queryByRole('alert')).toBeNull();
});

it.each(['HTTP 503', 'network'])('retains the profile and retries failed payouts after %s', async failure => {
  responses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(user('Synthetic Alpha'))));
  localStorage.setItem('access_token', 'synthetic-session-a');
  await act(async () => {
    payoutResponses.set('Bearer synthetic-session-a', Promise.resolve().then(() => {
      if (failure === 'network') throw new TypeError('Synthetic network failure');
      return new Response('{}', { status: 503 });
    }));
    mount();
  });
  const main = within(screen.getByRole('main'));
  expect(main.getByRole('heading', { name: 'Synthetic Alpha' })).toBeTruthy();
  expect(main.getByRole('heading', { name: accountMessages.en.catPayoutsLog })).toBeTruthy();
  expect(main.getByRole('alert').textContent).toContain(sharedMessages.en.couldNotLoadData);
  expect(main.queryByText(/no payouts/i)).toBeNull();
  let respond: (response: Response) => void = () => {};
  payoutResponses.set('Bearer synthetic-session-a', new Promise(resolve => { respond = resolve; }));
  await act(async () => { fireEvent.click(main.getByRole('button', { name: sharedMessages.en.retry })); });
  expect(main.getByRole('status').textContent).toMatch(/loading.*payout/i);
  expect(main.getByRole('heading', { name: 'Synthetic Alpha' })).toBeTruthy();
  await act(async () => { respond(Response.json(readPayouts)); });
  expect(main.queryByRole('alert')).toBeNull();
  expect(main.queryByText(/loading.*payout/i)).toBeNull();
  expect(main.queryByText(/no payouts/i)).toBeNull();
  readPayouts.forEach(payout => {
    expect(main.getByText(`${payout.amount.toLocaleString('en-US')} $ASPORE`)).toBeTruthy();
    expect(main.getByText(accountMessages.en[payout.status])).toBeTruthy();
  });
});

it('ignores a late payout retry after the owner changes', async () => {
  responses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(user('Synthetic Alpha'))));
  payoutResponses.set('Bearer synthetic-session-a', Promise.resolve(new Response('{}', { status: 503 })));
  localStorage.setItem('access_token', 'synthetic-session-a');
  await act(async () => { mount(); });
  let respond: (response: Response) => void = () => {};
  payoutResponses.set('Bearer synthetic-session-a', new Promise(resolve => { respond = resolve; }));
  const main = within(screen.getByRole('main'));
  await act(async () => { fireEvent.click(main.getByRole('button', { name: sharedMessages.en.retry })); });
  responses.set('Bearer synthetic-session-b', Promise.resolve(Response.json(user('Synthetic Beta'))));
  await act(async () => {
    localStorage.setItem('access_token', 'synthetic-session-b');
    window.dispatchEvent(new Event('storage'));
  });
  await act(async () => { respond(Response.json(readPayouts)); });
  const currentMain = within(screen.getByRole('main'));
  expect(currentMain.getByRole('heading', { name: 'Synthetic Beta' })).toBeTruthy();
  expect(currentMain.queryByRole('heading', { name: 'Synthetic Alpha' })).toBeNull();
  expect(currentMain.queryByText('1,111 $ASPORE')).toBeNull();
  expect(currentMain.queryByRole('alert')).toBeNull();
});

it('shows a pending deposit balance instead of an unverified zero', async () => {
  let respond: (response: Response) => void = () => {};
  responses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(user('Synthetic Alpha'))));
  balanceResponses.set('Bearer synthetic-session-a', new Promise(resolve => { respond = resolve; }));
  localStorage.setItem('access_token', 'synthetic-session-a');
  await act(async () => { mount(); });
  const main = within(screen.getByRole('main'));
  const deposit = within(main.getByRole('region', { name: /deposit balance/i }));
  expect(deposit.getByRole('status').textContent).toMatch(/loading.*balance/i);
  expect(deposit.queryByText('0')).toBeNull();
  await act(async () => { respond(Response.json({ aspore_balance: 7 })); });
  expect(deposit.getByText('7')).toBeTruthy();
  expect(deposit.queryByText(/loading.*balance/i)).toBeNull();
});

it.each([0, 7])('reads verified deposit balance %i independently of auth/me', async balance => {
  responses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(user('Synthetic Alpha'))));
  balanceResponses.set('Bearer synthetic-session-a', Promise.resolve(Response.json({ aspore_balance: balance })));
  localStorage.setItem('access_token', 'synthetic-session-a');
  await act(async () => { mount(); });
  const main = within(screen.getByRole('main'));
  const deposit = within(main.getByRole('region', { name: /deposit balance/i }));
  expect(deposit.getByText(String(balance))).toBeTruthy();
  expect(deposit.queryByRole('alert')).toBeNull();
});

it.each(['HTTP 503', 'network'])('retains the profile and retries deposit balance after %s', async failure => {
  responses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(user('Synthetic Alpha'))));
  localStorage.setItem('access_token', 'synthetic-session-a');
  await act(async () => {
    balanceResponses.set('Bearer synthetic-session-a', Promise.resolve().then(() => {
      if (failure === 'network') throw new TypeError('Synthetic network failure');
      return new Response('{}', { status: 503 });
    }));
    mount();
  });
  const main = within(screen.getByRole('main'));
  const deposit = within(main.getByRole('region', { name: /deposit balance/i }));
  expect(main.getByRole('heading', { name: 'Synthetic Alpha' })).toBeTruthy();
  expect(deposit.getByRole('alert').textContent).toContain(sharedMessages.en.couldNotLoadData);
  expect(deposit.queryByText('0')).toBeNull();
  balanceResponses.set('Bearer synthetic-session-a', Promise.resolve(Response.json({ aspore_balance: 7 })));
  await act(async () => { fireEvent.click(deposit.getByRole('button', { name: sharedMessages.en.retry })); });
  expect(deposit.getByText('7')).toBeTruthy();
  expect(deposit.queryByRole('alert')).toBeNull();
});

it('ignores a late deposit balance after the owner changes', async () => {
  let respond: (response: Response) => void = () => {};
  responses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(user('Synthetic Alpha'))));
  balanceResponses.set('Bearer synthetic-session-a', new Promise(resolve => { respond = resolve; }));
  localStorage.setItem('access_token', 'synthetic-session-a');
  await act(async () => { mount(); });
  responses.set('Bearer synthetic-session-b', Promise.resolve(Response.json(user('Synthetic Beta'))));
  balanceResponses.set('Bearer synthetic-session-b', Promise.resolve(Response.json({ aspore_balance: 7 })));
  await act(async () => {
    localStorage.setItem('access_token', 'synthetic-session-b');
    window.dispatchEvent(new Event('storage'));
  });
  await act(async () => { respond(Response.json({ aspore_balance: 987654 })); });
  const main = within(screen.getByRole('main'));
  const deposit = within(main.getByRole('region', { name: /deposit balance/i }));
  expect(main.getByRole('heading', { name: 'Synthetic Beta' })).toBeTruthy();
  expect(deposit.getByText('7')).toBeTruthy();
  expect(deposit.queryByText('987,654')).toBeNull();
});

it('keeps the sign-in state for an unauthorized profile response', async () => {
  responses.set('Bearer synthetic-session-a', Promise.resolve(new Response('{}', { status: 401 })));
  localStorage.setItem('access_token', 'synthetic-session-a');
  await act(async () => { mount(); });
  const main = within(screen.getByRole('main'));
  expect(main.getByRole('link', { name: accountMessages.en.signIn })).toBeTruthy();
  expect(main.queryByRole('alert')).toBeNull();
});

it.each([{}, { aspore_balance: -1 }, { aspore_balance: 1.5 }, { aspore_balance: Number.MAX_SAFE_INTEGER + 1 }, { aspore_balance: '7' }])(
  'reports malformed deposit balance %j without inventing zero', async payload => {
    responses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(user('Synthetic Alpha'))));
    balanceResponses.set('Bearer synthetic-session-a', Promise.resolve(Response.json(payload)));
    localStorage.setItem('access_token', 'synthetic-session-a');
    await act(async () => { mount(); });
    const main = within(screen.getByRole('main'));
    const deposit = within(main.getByRole('region', { name: /deposit balance/i }));
    expect(main.getByRole('heading', { name: 'Synthetic Alpha' })).toBeTruthy();
    expect(deposit.getByRole('alert').textContent).toContain(sharedMessages.en.couldNotLoadData);
    expect(deposit.queryByText('0')).toBeNull();
    expect(deposit.getByRole('button', { name: sharedMessages.en.retry })).toBeTruthy();
  },
);
