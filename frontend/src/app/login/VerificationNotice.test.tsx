// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { ResendVerification } from './VerificationNotice';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { AUTH_FLOW_MESSAGES } from '@/lib/i18n/authFlow';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('keeps resend disabled when the notice is remounted within the cooldown', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));
  const name = AUTH_FLOW_MESSAGES.en.resendAction;
  const first = render(<LocaleProvider initialLocale="en"><ResendVerification email="Remount@Example.invalid" /></LocaleProvider>);
  fireEvent.click(screen.getByRole('button', { name }));
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain(AUTH_FLOW_MESSAGES.en.resendWait));
  first.unmount();
  render(<LocaleProvider initialLocale="en"><ResendVerification email="remount@example.invalid" /></LocaleProvider>);
  expect((screen.getByRole('button', { name: /Send again in \d+ s/ }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole('status').textContent).toContain(AUTH_FLOW_MESSAGES.en.resendWait);
});

it.each(['en', 'ru'] as const)('counts the wait down in the button and announces only its start and end (%s)', async locale => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));
  const m = AUTH_FLOW_MESSAGES[locale];
  render(<LocaleProvider initialLocale={locale}><ResendVerification email={`count-${locale}@example.invalid`} /></LocaleProvider>);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: m.resendAction })); });
  const button = () => screen.getByRole('button') as HTMLButtonElement;
  expect(button().textContent).toBe(m.resendIn.replace('{seconds}', '60'));
  expect(button().disabled).toBe(true);
  const announced = screen.getByRole('status').textContent;
  expect(announced).toContain(m.resendWait);
  await act(async () => { vi.advanceTimersByTime(18_000); });
  expect(button().textContent).toBe(m.resendIn.replace('{seconds}', '42'));
  expect(screen.getByRole('status').textContent).toBe(announced);
  expect(button().closest('[aria-live]')).toBeNull();
  await act(async () => { vi.advanceTimersByTime(42_000); });
  expect(button().textContent).toBe(m.resendAction);
  expect(button().disabled).toBe(false);
  expect(screen.getByRole('status').textContent).toBe(m.resendReady);
});
