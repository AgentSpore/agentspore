// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import AgentsPage from './page';
import type { Agent } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { LanguageSelector } from '@/components/LanguageSelector';
import { publicMessages } from '@/lib/i18n/public';
import { docsMessages } from '@/lib/i18n/docs';
import { sharedMessages } from '@/lib/i18n/shared';
import { WORKSPACE_MESSAGES } from '@/lib/i18n/agentWorkspace';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => <LanguageSelector /> }));
const polled = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
const AGENT: Agent = {
  id: 'a1', name: 'Synthetic agent', handle: 'synthetic', agent_type: 'external', model_provider: 'x', model_name: 'y',
  specialization: 'programmer', skills: [], karma: 1, projects_created: 0, code_commits: 0, reviews_done: 0,
  last_heartbeat: null, is_active: true, created_at: '2026-01-01T00:00:00Z', dna_risk: 5, dna_speed: 5,
  dna_verbosity: 5, dna_creativity: 5, bio: null, fork_count: 0, is_hosted: false,
};
const listState = () => ({ data: [AGENT], error: null, loading: false, lastUpdated: null, refetch: vi.fn() });
const emptyState = () => ({ data: [], error: null, loading: false, lastUpdated: null, refetch: vi.fn() });
vi.mock('@/hooks/usePolledResource', () => ({ usePolledResource: () => polled.state }));
beforeEach(() => { polled.state = listState(); });
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

it.each(['en', 'ru'] as const)('does not show zero counters or freshness for an empty list in %s', locale => {
  polled.state = { ...emptyState(), lastUpdated: Date.now() };
  render(<LocaleProvider initialLocale={locale}><AgentsPage /></LocaleProvider>);
  expect(screen.queryByText(new RegExp(`^0 ${publicMessages[locale].registered}`))).toBeNull();
  expect(screen.queryByText(new RegExp(sharedMessages[locale].updated))).toBeNull();
});

it.each(['en', 'ru'] as const)('shows an error with a working retry instead of zeros in %s', locale => {
  const refetch = vi.fn();
  polled.state = { ...emptyState(), error: new Error('HTTP 500'), refetch };
  render(<LocaleProvider initialLocale={locale}><AgentsPage /></LocaleProvider>);
  expect(screen.getByRole('alert').textContent).toContain(sharedMessages[locale].couldNotLoadData);
  expect(screen.queryByText(new RegExp(`^0 ${publicMessages[locale].registered}`))).toBeNull();
  fireEvent.click(screen.getAllByRole('button', { name: sharedMessages[locale].retry })[0]!);
  expect(refetch).toHaveBeenCalled();
});

it.each([null, []])('shows exactly one retry on a failed first load (data: %j)', data => {
  polled.state = { ...emptyState(), data, error: new Error('HTTP 500') };
  render(<LocaleProvider initialLocale="en"><AgentsPage /></LocaleProvider>);
  expect(screen.getAllByRole('button', { name: sharedMessages.en.retry })).toHaveLength(1);
  expect(screen.getByRole('alert')).toBeTruthy();
});

it.each(['en', 'ru'] as const)('teaches and hides search and filters when no agent exists in %s', locale => {
  polled.state = emptyState();
  render(<LocaleProvider initialLocale={locale}><AgentsPage /></LocaleProvider>);
  expect(screen.queryByRole('textbox')).toBeNull();
  expect(screen.queryByRole('button', { name: publicMessages[locale].filterActiveAgents })).toBeNull();
  expect(screen.getByText(docsMessages[locale].eAgentsTitle)).toBeTruthy();
  const body = screen.getByText(docsMessages[locale].eAgentsBody).textContent ?? '';
  expect(body).toMatch(locale === 'en' ? /right after registration/ : /сразу после регистрации/);
  expect(body).not.toMatch(/heartbeat/);
  expect(screen.getAllByRole('link', { name: docsMessages[locale].eAction }).some(a => a.getAttribute('href') === '/docs/connect')).toBe(true);
});

it.each(['en', 'ru'] as const)('marks historical profiles without inventing model or DNA in %s', locale => {
  polled.state = { ...listState(), data: [{ ...AGENT, model_provider: null, model_name: null, created_at: null, dna_risk: null, dna_speed: null, dna_verbosity: null, dna_creativity: null, is_active: false, recovery_provenance: { source: 'legacy_map', source_url: 'https://github.com/Example/history' } }] };
  render(<LocaleProvider initialLocale={locale}><AgentsPage /></LocaleProvider>);
  expect(screen.getByText(locale === 'en' ? 'Historical profile' : 'Исторический профиль')).toBeTruthy();
  expect(screen.getByText(locale === 'en' ? /Original model unknown/ : /Исходная модель неизвестна/)).toBeTruthy();
  expect(screen.getByText(locale === 'en' ? 'DNA unknown' : 'DNA неизвестна')).toBeTruthy();
});
