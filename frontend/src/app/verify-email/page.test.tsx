// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import VerifyEmailPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';
import { AUTH_FLOW_MESSAGES } from '@/lib/i18n/authFlow';

const navigation = vi.hoisted(() => ({ query: 'token=synthetic-test-code' }));
vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams(navigation.query), useRouter: () => ({ refresh: vi.fn() }) }));

beforeEach(() => { navigation.query = 'token=synthetic-test-code'; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const failed = (detail = 'Invalid or expired verification link.') =>
  vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 400 }));

it('explains an unusable link in plain words, in both languages, without a terminal label', async () => {
  const fetchMock = failed();
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale="en"><VerifyEmailPage /></LocaleProvider>);
  expect((await screen.findByRole('heading', { name: AUTH_FLOW_MESSAGES.en.linkTitle })).textContent).toBe('This link no longer works');
  expect(screen.getByText(AUTH_FLOW_MESSAGES.en.linkReason)).toBeTruthy();
  expect(screen.queryByText(/verify@agentspore/)).toBeNull();
  expect(screen.queryByText(/Invalid or expired verification link/)).toBeNull();
  expect(screen.getByRole('link', { name: AUTH_FLOW_MESSAGES.en.linkSignIn }).getAttribute('href')).toBe('/login');
  fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
  expect(screen.getByRole('heading', { name: AUTH_FLOW_MESSAGES.ru.linkTitle })).toBeTruthy();
  expect(screen.getByText(AUTH_FLOW_MESSAGES.ru.linkReason)).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('asks a signed-out person for the address and sends a new link to it', async () => {
  const fetchMock = failed();
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale="en"><VerifyEmailPage /></LocaleProvider>);
  const email = await screen.findByRole('textbox', { name: accountMessages.en.emailAddress });
  fireEvent.change(email, { target: { value: 'Synthetic@Example.Invalid' } });
  fireEvent.click(screen.getByRole('button', { name: AUTH_FLOW_MESSAGES.en.linkNewLink }));
  await screen.findByTestId('state-resent');
  expect(fetchMock).toHaveBeenLastCalledWith(expect.stringContaining('/auth/resend-verification'),
    expect.objectContaining({ body: JSON.stringify({ email: 'synthetic@example.invalid' }) }));
});

function signIn(profile: Record<string, unknown>, email = 'signed@example.invalid') {
  const store: Record<string, string> = { access_token: 'synthetic-session' };
  vi.stubGlobal('localStorage', { getItem: (k: string) => store[k] ?? null, setItem: vi.fn(), removeItem: vi.fn() });
  const fetchMock = vi.fn(async (url: string) => String(url).includes('/auth/me')
    ? new Response(JSON.stringify({ id: 'u1', name: 'Synthetic', email, avatar_url: null, solana_wallet: null,
      token_balance: 0, created_at: '2026-01-01T00:00:00Z', ...profile }), { status: 200 })
    : new Response(JSON.stringify({ detail: 'Invalid or expired verification link.' }), { status: 400 }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

it.each(['en', 'ru'] as const)('resends to the signed-in address without asking for it (%s)', async locale => {
  const email = `signed-${locale}@example.invalid`;
  const fetchMock = signIn({ is_verified: false }, email);
  render(<LocaleProvider initialLocale={locale}><VerifyEmailPage /></LocaleProvider>);
  expect(await screen.findByText(AUTH_FLOW_MESSAGES[locale].linkSendTo.replace('{email}', email))).toBeTruthy();
  expect(screen.queryByRole('textbox')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: AUTH_FLOW_MESSAGES[locale].resendAction }));
  await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(expect.stringContaining('/auth/resend-verification'),
    expect.objectContaining({ body: JSON.stringify({ email }) })));
});

it.each(['en', 'ru'] as const)('says the email is already confirmed and links to the profile (%s)', async locale => {
  signIn({ is_verified: true });
  render(<LocaleProvider initialLocale={locale}><VerifyEmailPage /></LocaleProvider>);
  expect(await screen.findByRole('heading', { name: AUTH_FLOW_MESSAGES[locale].linkConfirmedTitle })).toBeTruthy();
  expect(screen.getByRole('link', { name: AUTH_FLOW_MESSAGES[locale].linkToProfile }).getAttribute('href')).toBe('/profile');
  expect(screen.queryByText(AUTH_FLOW_MESSAGES[locale].linkTitle)).toBeNull();
});

it('keeps the network failure message for a request that never reached the server', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Synthetic network unavailable')));
  render(<LocaleProvider initialLocale="en"><VerifyEmailPage /></LocaleProvider>);
  expect((await screen.findByTestId('error-message')).textContent).toBe(accountMessages.en.networkErrorCouldNotReachTheServer);
});

it('submits a normalized email through a native form with email autofill', async () => {
  navigation.query = '';
  const fetchMock = vi.fn().mockResolvedValue(new Response('{}'));
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale="en"><VerifyEmailPage /></LocaleProvider>);
  const email = screen.getByRole('textbox', { name: accountMessages.en.emailAddress });
  const form = email.closest('form');
  expect(form).not.toBeNull();
  if (!form) throw new Error('Resend form missing');
  expect(email.getAttribute('autocomplete')).toBe('email');
  fireEvent.change(email, { target: { value: 'Synthetic@Example.Invalid' } });
  fireEvent.submit(form);
  await screen.findByTestId('state-resent');
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/auth/resend-verification'),
    expect.objectContaining({ method: 'POST', body: JSON.stringify({ email: 'synthetic@example.invalid' }) }));
});

it.each([200, 503, 'network'] as const)('keeps resend account-neutral after %s', async outcome => {
  navigation.query = '';
  const fetchMock = vi.fn().mockImplementation(() => outcome === 'network'
    ? Promise.reject(new TypeError('Synthetic network unavailable'))
    : Promise.resolve(new Response('{}', { status: outcome })));
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale="en"><VerifyEmailPage /></LocaleProvider>);
  fireEvent.change(screen.getByRole('textbox', { name: accountMessages.en.emailAddress }),
    { target: { value: 'synthetic@example.invalid' } });
  fireEvent.click(screen.getByRole('button', { name: AUTH_FLOW_MESSAGES.en.linkNewLink }));
  expect(await screen.findByTestId('state-resent')).toBeTruthy();
  expect(screen.getByText(accountMessages.en.ifTheAccountExistsAndIsUnverifiedA)).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('redirects to the profile with the email-verified marker after success', async () => {
  const destination = { href: '', origin: window.location.origin };
  vi.stubGlobal('window', new Proxy(window, {
    get: (target, property) => property === 'location' ? destination : Reflect.get(target, property),
  }));
  vi.stubGlobal('localStorage', { setItem: vi.fn(), getItem: vi.fn(() => null) });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ access_token: 'synthetic-test-session' }), { status: 200 })));
  render(<LocaleProvider initialLocale="en"><VerifyEmailPage /></LocaleProvider>);
  await screen.findByTestId('state-success');
  await waitFor(() => expect(destination.href).toMatch(/\/profile\?email=verified$/), { timeout: 5000 });
});
