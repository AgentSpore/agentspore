// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ResendVerification } from './VerificationNotice';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { AUTH_FLOW_MESSAGES } from '@/lib/i18n/authFlow';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('keeps resend disabled when the notice is remounted within the cooldown', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));
  const name = AUTH_FLOW_MESSAGES.en.resendAction;
  const first = render(<LocaleProvider initialLocale="en"><ResendVerification email="Remount@Example.invalid" /></LocaleProvider>);
  fireEvent.click(screen.getByRole('button', { name }));
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain(AUTH_FLOW_MESSAGES.en.resendWait));
  first.unmount();
  render(<LocaleProvider initialLocale="en"><ResendVerification email="remount@example.invalid" /></LocaleProvider>);
  expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole('status').textContent).toContain(AUTH_FLOW_MESSAGES.en.resendWait);
});
