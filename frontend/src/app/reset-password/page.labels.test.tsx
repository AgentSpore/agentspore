// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ForgotPasswordPage from '../forgot-password/page';
import ResetPasswordPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }), usePathname: () => '/' }));

afterEach(() => { cleanup(); window.history.replaceState({}, '', '/'); });

const COPY = {
  en: { back: 'Back to sign in', forgot: 'Reset your password', invalid: 'This link is not valid', again: 'Request a new link', fresh: 'Choose a new password' },
  ru: { back: 'Вернуться ко входу', forgot: 'Сброс пароля', invalid: 'Ссылка недействительна', again: 'Запросить новую ссылку', fresh: 'Задайте новый пароль' },
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
