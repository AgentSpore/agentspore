// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ErrorBoundary from './ErrorBoundary';
import { LanguageSelector } from './LanguageSelector';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { sharedMessages } from '@/lib/i18n/shared';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('translates the caught fallback, preserves unknown error detail and retries', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  let fail = true;
  function SyntheticFailure() {
    if (fail) throw new Error('Online');
    return <p>Recovered synthetic fixture</p>;
  }
  render(<LocaleProvider initialLocale="en"><LanguageSelector /><ErrorBoundary><SyntheticFailure /></ErrorBoundary></LocaleProvider>);
  expect(screen.getByText(sharedMessages.en.somethingWentWrong)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
  expect(screen.getByText(sharedMessages.ru.somethingWentWrong)).toBeTruthy();
  expect(screen.getByText('Online')).toBeTruthy();
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: sharedMessages.ru.tryAgain }));
  expect(screen.getByText('Recovered synthetic fixture')).toBeTruthy();
});
