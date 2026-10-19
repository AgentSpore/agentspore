// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import DashboardPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { sharedMessages } from '@/lib/i18n/shared';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));

const STATS = { active_agents: 7, total_projects: 3, total_code_commits: 5, projects_deployed: 1, total_prs_merged: 2 };
const api = vi.hoisted(() => ({ statsOk: true }));

beforeEach(() => {
  vi.useFakeTimers();
  api.statsOk = true;
  vi.stubGlobal('EventSource', class { onmessage = null; close() {} });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (String(url).includes('/agents/stats')) return api.statsOk
      ? { ok: true, json: async () => STATS } : { ok: false, status: 500, json: async () => ({}) };
    return { ok: true, json: async () => [] };
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const mount = async (locale: 'en' | 'ru') => {
  render(<LocaleProvider initialLocale={locale}><DashboardPage /></LocaleProvider>);
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
};

it.each(['en', 'ru'] as const)('shows one retryable error and no stat cards when the first load fails in %s', async locale => {
  api.statsOk = false;
  await mount(locale);
  expect(screen.getByRole('alert').textContent).toContain(sharedMessages[locale].couldNotLoadData);
  expect(document.getElementById('stats')).toBeNull();
});

it.each(['en', 'ru'] as const)('flags stale numbers with a retry when a later poll fails in %s', async locale => {
  await mount(locale);
  expect(document.getElementById('stats')).not.toBeNull();
  expect(screen.queryByText(new RegExp(sharedMessages[locale].staleLastUpdated))).toBeNull();
  api.statsOk = false;
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(screen.getByText(new RegExp(sharedMessages[locale].staleLastUpdated))).toBeTruthy();
  expect(document.getElementById('stats')).not.toBeNull();
  api.statsOk = true;
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: sharedMessages[locale].retry })); await vi.advanceTimersByTimeAsync(10); });
  expect(screen.queryByText(new RegExp(sharedMessages[locale].staleLastUpdated))).toBeNull();
});
