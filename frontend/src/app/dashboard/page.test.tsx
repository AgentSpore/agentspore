// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import DashboardPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { renderToString } from 'react-dom/server';
import { sharedMessages } from '@/lib/i18n/shared';
import { publicMessages } from '@/lib/i18n/public';

const navigation = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => navigation, usePathname: () => '/dashboard' }));

const STATS = { active_agents: 7, total_projects: 3, total_code_commits: 5, projects_deployed: 1, total_prs_merged: 2 };
const api = vi.hoisted(() => ({ statsOk: true, activeAgents: 7, external: null as Promise<Response> | null }));

beforeEach(() => {
  vi.useFakeTimers();
  api.statsOk = true;
  api.activeAgents = 7;
  api.external = null;
  navigation.replace.mockClear();
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } });
  vi.stubGlobal('EventSource', class { onmessage = null; close() {} });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (String(url).includes('/external-agents') && api.external) return api.external;
    if (String(url).endsWith('/auth/me')) return { ok: false, status: 503, json: async () => null };
    if (String(url).includes('/agents/stats')) return api.statsOk
      ? { ok: true, json: async () => ({ ...STATS, active_agents: api.activeAgents }) } : { ok: false, status: 500, json: async () => ({}) };
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
  api.activeAgents = 7;
  api.external = null;
  navigation.replace.mockClear();
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: sharedMessages[locale].retry })); await vi.advanceTimersByTimeAsync(10); });
  expect(screen.queryByText(new RegExp(sharedMessages[locale].staleLastUpdated))).toBeNull();
});

it('immediately resets a positive counter when the next target is zero and cancels its timer', async () => {
  await mount('en');
  await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
  expect(document.getElementById('stats')?.textContent).toContain('7');
  api.activeAgents = 0;
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(document.getElementById('stats')?.firstElementChild?.querySelector('.text-2xl')?.textContent).toBe('0');
  await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
  expect(document.getElementById('stats')?.firstElementChild?.querySelector('.text-2xl')?.textContent).toBe('0');
});

it('observes sign-in and sign-out without keeping a stale private result', async () => {
  await mount('en');
  expect(screen.getByText('Not signed in')).toBeTruthy();
  await act(async () => { localStorage.setItem('access_token', 'synthetic-session-a'); window.dispatchEvent(new Event('storage')); });
  expect(screen.queryByText('Not signed in')).toBeNull();
  await act(async () => { localStorage.removeItem('access_token'); window.dispatchEvent(new Event('storage')); });
  expect(screen.getByText('Not signed in')).toBeTruthy();
});

it('ignores an old session 401 after a new sign-in', async () => {
  let respond: (response: Response) => void = () => {};
  api.external = new Promise(resolve => { respond = resolve; });
  localStorage.setItem('access_token', 'synthetic-session-a');
  await mount('en');
  api.external = Promise.resolve(Response.json([]));
  await act(async () => { localStorage.setItem('access_token', 'synthetic-session-b'); window.dispatchEvent(new Event('storage')); });
  await act(async () => { respond(new Response(null, { status: 401 })); });
  expect(localStorage.getItem('access_token')).toBe('synthetic-session-b');
  expect(navigation.replace).not.toHaveBeenCalled();
});

it('renders anonymous when browser storage is denied', async () => {
  vi.stubGlobal('localStorage', { getItem() { throw new DOMException('Synthetic denial', 'SecurityError'); } });
  await mount('en');
  expect(screen.getByText('Not signed in')).toBeTruthy();
});

it('cancels an unfinished target animation when a locale reload changes the target, then cleans up on unmount', async () => {
  api.activeAgents = 100;
  const view = render(<LocaleProvider initialLocale="en"><DashboardPage /></LocaleProvider>);
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  api.activeAgents = 3;
  await act(async () => { fireEvent.click(screen.getAllByRole('button', { name: 'Русский' })[0]); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
  expect(document.getElementById('stats')?.firstElementChild?.querySelector('.text-2xl')?.textContent).toBe('3');
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});

it('clears and redirects the current unauthorized session', async () => {
  api.external = Promise.resolve(new Response(null, { status: 401 }));
  localStorage.setItem('access_token', 'synthetic-session-a');
  await mount('en');
  expect(localStorage.getItem('access_token')).toBeNull();
  expect(navigation.replace).toHaveBeenCalledWith('/login');
});

it('keeps the pending server snapshot without reading browser storage', () => {
  const read = vi.fn(() => { throw new Error('SSR must not read storage'); });
  vi.stubGlobal('localStorage', { getItem: read });
  expect(renderToString(<LocaleProvider initialLocale="en"><DashboardPage /></LocaleProvider>)).not.toContain('Not signed in');
  expect(read).not.toHaveBeenCalled();
});

it('starts the animation at zero when a cleared target returns to its previous value', async () => {
  await mount('en');
  await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
  api.activeAgents = 0;
  await act(async () => { fireEvent.click(screen.getAllByRole('button', { name: 'Русский' })[0]); });
  api.activeAgents = 7;
  await act(async () => { fireEvent.click(screen.getAllByRole('button', { name: 'English' })[0]); });
  expect(document.getElementById('stats')?.firstElementChild?.querySelector('.text-2xl')?.textContent).toBe('0');
  await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
  expect(document.getElementById('stats')?.firstElementChild?.querySelector('.text-2xl')?.textContent).toBe('7');
});

it.each(['en', 'ru'] as const)('keeps imported unknown-author activity usable in %s', async locale => {
  const original = globalThis.fetch;
  vi.stubGlobal('fetch', vi.fn(async (...args: Parameters<typeof fetch>) => {
    if (String(args[0]).includes('/activity?')) return new Response(JSON.stringify([{ agent_id: null, agent_name: 'Unknown author', action_type: 'code_commit', description: 'Historical change', ts: '2026-01-01T00:00:00Z', metadata: { source: 'github_public_history', github_url: 'https://github.com/Example/repo/commit/abc' } }]));
    return original(...args);
  }));
  await mount(locale);
  expect(screen.getByText(locale === 'en' ? 'Unknown author' : 'Автор неизвестен')).toBeTruthy();
  expect(screen.getByText(locale === 'en' ? 'Imported history' : 'Восстановленная история')).toBeTruthy();
  expect(screen.getByRole('link', { name: locale === 'en' ? 'History source' : 'Источник истории' }).getAttribute('href')).toBe('https://github.com/Example/repo/commit/abc');
  expect(document.querySelector('a[href="/agents/null"]')).toBeNull();
});

it.each(['en', 'ru'] as const)('shows the live marker only on non-zero stat cards in %s', async locale => {
  api.activeAgents = 0;
  await mount(locale);
  const live = publicMessages[locale].live;
  const cards = [...document.querySelectorAll('#stats .stat-card')];
  expect(cards).toHaveLength(5);
  expect(cards[0]?.textContent).not.toContain(live);
  expect(cards.filter(card => card.textContent?.includes(live))).toHaveLength(4);
});

it('lays the five stat cards out without an orphan row', async () => {
  await mount('en');
  expect(document.getElementById('stats')?.className).toContain('md:grid-cols-5');
  const cards = [...document.querySelectorAll('#stats .stat-card')];
  expect(cards.at(-1)?.classList.contains('last:col-span-2')).toBe(true);
  expect(cards.at(-1)?.classList.contains('md:last:col-span-1')).toBe(true);
  expect(cards.every(card => !card.classList.contains('col-span-2'))).toBe(true);
});
