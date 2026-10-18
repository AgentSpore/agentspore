// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AgentsPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { LanguageSelector } from '@/components/LanguageSelector';
import { publicMessages } from '@/lib/i18n/public';
import { WORKSPACE_MESSAGES } from '@/lib/i18n/agentWorkspace';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => <LanguageSelector /> }));
vi.mock('@/hooks/usePolledResource', () => ({ usePolledResource: () => ({ data: [], error: null, loading: false, lastUpdated: null, refetch: vi.fn() }) }));
afterEach(cleanup);

it.each(['en', 'ru'] as const)('names agent search in %s', locale => {
  render(<LocaleProvider initialLocale={locale}><AgentsPage /></LocaleProvider>);
  fireEvent.change(screen.getByRole('textbox', { name: publicMessages[locale].search }), { target: { value: 'Synthetic agent' } });
  expect(screen.getByRole('textbox', { name: publicMessages[locale].search })).toHaveProperty('value', 'Synthetic agent');
});

it.each(['en', 'ru'] as const)('connects an external agent in %s', locale => {
  render(<LocaleProvider initialLocale={locale}><AgentsPage /></LocaleProvider>);
  expect(screen.getByRole('link', { name: new RegExp(WORKSPACE_MESSAGES[locale].connect) })).toHaveProperty('href', expect.stringContaining('/workspace#connect-agent'));
  expect(screen.getByText(WORKSPACE_MESSAGES[locale].setupIntro)).toBeTruthy();
  expect(screen.queryByText(publicMessages[locale].runYourAgentOnAgentSporeInfrastructureNoSetup)).toBeNull();
});

it('translates filter labels while keeping the active filter and search draft', () => {
  render(<LocaleProvider initialLocale="en"><AgentsPage /></LocaleProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Active' }));
  const search = screen.getByRole('textbox');
  fireEvent.change(search, { target: { value: 'Online' } });
  fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
  expect(screen.getByRole('button', { name: 'Все' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Активные' }).getAttribute('aria-pressed')).toBe('true');
  expect((search as HTMLInputElement).value).toBe('Online');
  expect(screen.getByText(publicMessages.ru.noAgentsMatchYourFilter)).toBeTruthy();
});


it.each(['en', 'ru'] as const)('exposes the selected agent filter in %s', locale => {
  render(<LocaleProvider initialLocale={locale}><AgentsPage /></LocaleProvider>);
  const all = screen.getByRole('button', { name: publicMessages[locale].filterAllAgents });
  const active = screen.getByRole('button', { name: publicMessages[locale].filterActiveAgents });
  expect(all.getAttribute('aria-pressed')).toBe('true');
  expect(active.getAttribute('aria-pressed')).toBe('false');
  fireEvent.click(active);
  expect(all.getAttribute('aria-pressed')).toBe('false');
  expect(active.getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(all);
  expect(all.getAttribute('aria-pressed')).toBe('true');
  expect(active.getAttribute('aria-pressed')).toBe('false');
});
