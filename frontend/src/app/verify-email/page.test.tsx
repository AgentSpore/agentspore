// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import VerifyEmailPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';

vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams('token=synthetic-test-code'), useRouter: () => ({ refresh: vi.fn() }) }));

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
