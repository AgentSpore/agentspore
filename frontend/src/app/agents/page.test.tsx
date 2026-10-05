// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AgentsPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { LanguageSelector } from '@/components/LanguageSelector';
import { publicMessages } from '@/lib/i18n/public';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => <LanguageSelector /> }));
vi.mock('@/hooks/usePolledResource', () => ({ usePolledResource: () => ({ data: [], error: null, loading: false, lastUpdated: null, refetch: vi.fn() }) }));
afterEach(cleanup);

it('translates filter labels while keeping the active filter and search draft', () => {
  render(<LocaleProvider initialLocale="en"><AgentsPage /></LocaleProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Active' }));
  const search = screen.getByRole('textbox');
  fireEvent.change(search, { target: { value: 'Online' } });
  fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
  expect(screen.getByRole('button', { name: 'Все' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Активные' })).toBeTruthy();
  expect((search as HTMLInputElement).value).toBe('Online');
  expect(screen.getByText(publicMessages.ru.noAgentsMatchYourFilter)).toBeTruthy();
});
