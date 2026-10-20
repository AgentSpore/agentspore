// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import VerifyEmailPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';

const navigation = vi.hoisted(() => ({ query: 'token=synthetic-test-code' }));
vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams(navigation.query), useRouter: () => ({ refresh: vi.fn() }) }));

beforeEach(() => { navigation.query = 'token=synthetic-test-code'; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('translates a verification failure without repeating the verification request', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) });
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale="en"><VerifyEmailPage /></LocaleProvider>);
  await waitFor(() => expect(screen.getByText(accountMessages.en.verificationFailedTheLinkMayBeInvalid)).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
  expect(screen.getByText(accountMessages.ru.verificationFailedTheLinkMayBeInvalid)).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('localizes the backend invalid or expired verification detail in Russian', async () => {
  const detail = 'Invalid or expired verification link.';
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 400 })));
  render(<LocaleProvider initialLocale="ru"><VerifyEmailPage /></LocaleProvider>);
  await screen.findByTestId('state-error');
  expect(screen.queryByText(detail)).toBeNull();
  expect(screen.getByText('Ссылка подтверждения недействительна или устарела.')).toBeTruthy();
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
  fireEvent.click(screen.getByRole('button', { name: accountMessages.en.resend }));
  expect(await screen.findByTestId('state-resent')).toBeTruthy();
  expect(screen.getByText(accountMessages.en.ifTheAccountExistsAndIsUnverifiedA)).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
