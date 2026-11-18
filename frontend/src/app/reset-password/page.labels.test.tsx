// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ForgotPasswordPage from '../forgot-password/page';
import ResetPasswordPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }), usePathname: () => '/' }));

afterEach(() => { cleanup(); window.history.replaceState({}, '', '/'); });

const COPY = {
  en: { back: 'Back to sign in', forgot: 'Reset your password', invalid: 'This link is not valid', again: 'Request a new link', fresh: 'Choose a new password', done: 'Password changed', pw: 'New password', confirm: 'Confirm password' },
  ru: { back: 'Вернуться ко входу', forgot: 'Сброс пароля', invalid: 'Ссылка недействительна', again: 'Запросить новую ссылку', fresh: 'Задайте новый пароль', done: 'Готово', pw: 'Новый пароль', confirm: 'Повторите пароль' },
} as const;

const noTerminalText = () => expect(document.body.textContent).not.toMatch(/cd ~|\$ [a-zа-я]/);

it.each(['en', 'ru'] as const)('forgot-password uses plain labels in %s', locale => {
  render(<LocaleProvider initialLocale={locale}><ForgotPasswordPage /></LocaleProvider>);
  expect(screen.getByRole('heading', { name: COPY[locale].forgot })).toBeTruthy();
  expect(screen.getByRole('link', { name: COPY[locale].back })).toBeTruthy();
  noTerminalText();
});

it.each(['en', 'ru'] as const)('reset-password without token uses plain labels in %s', locale => {
  render(<LocaleProvider initialLocale={locale}><ResetPasswordPage /></LocaleProvider>);
  expect(screen.getByRole('heading', { name: COPY[locale].invalid })).toBeTruthy();
  expect(screen.getByRole('link', { name: COPY[locale].again })).toBeTruthy();
  expect(screen.getByRole('link', { name: COPY[locale].back })).toBeTruthy();
  noTerminalText();
});

it.each(['en', 'ru'] as const)('reset-password with token uses plain heading in %s', async locale => {
  window.history.replaceState({}, '', '/reset-password?token=abc');
  render(<LocaleProvider initialLocale={locale}><ResetPasswordPage /></LocaleProvider>);
  expect(await screen.findByRole('heading', { name: COPY[locale].fresh })).toBeTruthy();
  noTerminalText();
});

it.each(['en', 'ru'] as const)('reset-password shows plain form labels and success heading in %s', async locale => {
  window.history.replaceState({}, '', '/reset-password?token=abc');
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));
  render(<LocaleProvider initialLocale={locale}><ResetPasswordPage /></LocaleProvider>);
  fireEvent.change(await screen.findByLabelText(COPY[locale].pw), { target: { value: 'longenough1' } });
  fireEvent.change(screen.getByLabelText(COPY[locale].confirm), { target: { value: 'longenough1' } });
  fireEvent.submit(screen.getByLabelText(COPY[locale].pw).closest('form') as HTMLFormElement);
  await waitFor(() => expect(screen.getByRole('heading', { name: COPY[locale].done })).toBeTruthy());
  noTerminalText();
  vi.unstubAllGlobals();
});

const brandOnlyMono = () => {
  expect(document.body.textContent).not.toMatch(/(profile|reset)@agentspore/);
  const stray = [...document.querySelectorAll('.font-mono')].filter(el => !el.closest('a[href="/"]'));
  expect(stray.map(el => el.textContent)).toEqual([]);
};

it.each(['en', 'ru'] as const)('forgot-password has no terminal chrome and sans prose in %s', locale => {
  render(<LocaleProvider initialLocale={locale}><ForgotPasswordPage /></LocaleProvider>);
  brandOnlyMono();
});

it.each(['en', 'ru'] as const)('reset-password has no terminal chrome and sans prose in %s', async locale => {
  window.history.replaceState({}, '', '/reset-password?token=abc');
  render(<LocaleProvider initialLocale={locale}><ResetPasswordPage /></LocaleProvider>);
  await screen.findByRole('heading', { name: COPY[locale].fresh });
  brandOnlyMono();
});
