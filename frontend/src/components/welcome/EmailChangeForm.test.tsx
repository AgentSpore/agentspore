// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { emailChangeMessages as m } from '@/lib/i18n/emailChange';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { EmailChangeForm } from './EmailChangeForm';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

function submit(fetchMock: ReturnType<typeof vi.fn>, password = '') {
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale="en"><EmailChangeForm token="synthetic-owner" /></LocaleProvider>);
  fireEvent.change(screen.getByLabelText(m.en.newEmail), { target: { value: ' New@Example.org ' } });
  if (password) fireEvent.change(screen.getByLabelText(m.en.password), { target: { value: password } });
  fireEvent.click(screen.getByRole('button', { name: m.en.submit }));
}

it('sends the trimmed address and the password when given', async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => json(202, { message: 'If the address can be used, a confirmation link was sent to it.' }));
  submit(fetchMock, 'synthetic-pass');
  expect(await screen.findByText(m.en.sentNew)).toBeTruthy();
  expect(fetchMock.mock.calls[0]).toMatchObject([expect.stringContaining('/users/me/email/change'),
    { method: 'POST', body: JSON.stringify({ new_email: 'New@Example.org', current_password: 'synthetic-pass' }) }]);
  expect((screen.getByLabelText(m.en.password) as HTMLInputElement).value).toBe('');
});

it('sends no password for password-less accounts and shows the two-step text', async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => json(202, { message: 'Approve the change from your current address first, then confirm the new one.' }));
  submit(fetchMock);
  expect(await screen.findByText(m.en.sentOld)).toBeTruthy();
  expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)).current_password).toBeNull();
});

it.each([
  [403, 'Current password is incorrect', 'wrongPassword'], [403, 'Confirm your current email first', 'needsProof'],
  [409, 'Your welcome reward is being processed', 'rewardPending'], [422, 'Disposable email addresses are not accepted', 'disposable'],
  [429, 'Please wait', 'cooldown'], [401, 'x', 'signIn'], [503, 'x', 'unavailable'],
] as const)('maps %s %s to plain text', async (status, detail, key) => {
  submit(vi.fn(async () => json(status, { detail })));
  expect((await screen.findByRole('alert')).textContent).toBe(m.en[key]);
  expect(screen.queryByText(detail)).toBeNull();
  expect((screen.getByRole('button', { name: m.en.submit }) as HTMLButtonElement).disabled).toBe(false);
});

it('recovers from a network error so the owner can retry', async () => {
  submit(vi.fn().mockRejectedValue(new TypeError('offline')));
  expect((await screen.findByRole('alert')).textContent).toBe(m.en.unavailable);
});
