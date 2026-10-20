// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppRouterContext } from 'next/dist/shared/lib/app-router-context.shared-runtime';
import ProjectsPage from './page';
import { API_URL, type Project } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { displayPublicLabel, publicMessages } from '@/lib/i18n/public';
import { docsMessages } from '@/lib/i18n/docs';
import { sharedMessages } from '@/lib/i18n/shared';

vi.mock('@/components/Header', () => ({ Header: () => null }));
type Locale = 'en' | 'ru';
const NOW = '2026-10-20T12:00:00Z';
const PRIVATE_FAILURE = 'Synthetic internal project read failure';
const router = { back: vi.fn(), forward: vi.fn(), refresh: vi.fn(), push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), bfcacheId: 'synthetic-route' };
const BASE: Project = { id: 'old', title: 'Orbit Old', description: 'Shared signal', category: 'fintech', status: 'building',
  votes_up: 100, votes_down: 99, score: 1, repo_url: null, deploy_url: null, tech_stack: [], hackathon_id: null,
  creator_agent_id: 'synthetic-agent', agent_name: 'Veteran', agent_handle: 'synthetic-agent', github_stars: 900, created_at: '2026-10-10T12:00:00Z' };
const PROJECTS: Project[] = [BASE,
  { ...BASE, id: 'latest', title: 'Orbit Latest', agent_name: 'Ada', votes_up: 4, votes_down: 2, github_stars: 3, created_at: '2026-10-20T10:00:00Z' },
  { ...BASE, id: 'middle', title: 'Orbit Middle', description: 'Signal bridge', agent_name: 'Orion', votes_up: 30, votes_down: 40, github_stars: 50, created_at: '2026-10-18T12:00:00Z' },
  { ...BASE, id: 'runner', title: 'Orbit Runner', description: 'Independent', agent_name: 'Shared signal', votes_up: 8, votes_down: 2, github_stars: 20, created_at: '2026-10-20T02:00:00Z' },
];
const hiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden');
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(NOW));
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
});
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.useRealTimers();
  if (hiddenDescriptor) Object.defineProperty(document, 'hidden', hiddenDescriptor);
  else Reflect.deleteProperty(document, 'hidden');
});
function deferredResponse() {
  let resolve!: (response: Response) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<Response>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function projectsHTTP() {
  const plans: { query: string; promise: Promise<Response> }[] = [], requests: string[] = [], unexpected: string[] = [];
  const hold = (query = '?limit=100') => { const pending = deferredResponse(); plans.push({ query, promise: pending.promise }); return pending; };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL), plan = plans.shift();
    if (url.pathname !== '/api/v1/projects' || (init?.method ?? 'GET') !== 'GET' || new Headers(init?.headers).has('Authorization') || !plan || url.search !== plan.query) {
      unexpected.push(url.pathname + url.search); throw new Error('Unexpected synthetic request');
    }
    requests.push(url.search); return plan.promise;
  }));
  return { hold, requests, unexpected };
}
function openProjects(locale: Locale) {
  return render(<AppRouterContext.Provider value={router}><LocaleProvider initialLocale={locale}><ProjectsPage /></LocaleProvider></AppRouterContext.Provider>);
}
async function finish(pending: ReturnType<typeof deferredResponse>) {
  await act(async () => pending.resolve(Response.json(PROJECTS)));
}
function projectIds() {
  return [...screen.getByRole('main').querySelectorAll('article h3 a')].map(link => link.getAttribute('href')?.split('/').at(-1));
}
function localOption(locale: Locale, key: string) {
  const button = screen.getByRole('button', { name: displayPublicLabel(locale, key) });
  fireEvent.click(button); return button;
}
function timeOption(locale: Locale, key: string) {
  const group = screen.getByRole('button', { name: displayPublicLabel(locale, '24h') }).parentElement;
  if (!group) throw new Error('Missing time control group');
  const button = within(group).getByRole('button', { name: displayPublicLabel(locale, key) });
  fireEvent.click(button); return button;
}
function serverOption(locale: Locale, group: 'projectCategoryFilter' | 'projectStatusFilter', key: string) {
  const button = within(screen.getByRole('group', { name: docsMessages[locale][group] })).getByRole('button', { name: displayPublicLabel(locale, key) });
  fireEvent.click(button); return button;
}

it.each(['en', 'ru'] as const)('PL.HTTP composes local search/time/newest/stars/net-votes in %s without requests', async locale => {
  const http = projectsHTTP(), initial = http.hold(); openProjects(locale); await finish(initial);
  expect(projectIds()).toEqual(['latest', 'runner', 'middle', 'old']);
  localOption(locale, 'stars'); expect(projectIds()).toEqual(['old', 'middle', 'runner', 'latest']);
  localOption(locale, 'votes'); expect(projectIds()).toEqual(['runner', 'latest', 'old', 'middle']);
  timeOption(locale, '24h'); expect(projectIds()).toEqual(['runner', 'latest']);
  timeOption(locale, 'all'); const search = screen.getByRole('textbox', { name: publicMessages[locale].search });
  for (const [value, expected] of [['LATEST', 'latest'], ['SIGNAL BRIDGE', 'middle'], ['ORION', 'middle']]) {
    fireEvent.change(search, { target: { value } }); expect(projectIds()).toEqual([expected]);
  }
  fireEvent.change(search, { target: { value: 'Orbit' } }); timeOption(locale, '7d'); localOption(locale, 'new');
  expect(projectIds()).toEqual(['latest', 'runner', 'middle']);
  expect(http.requests).toEqual(['?limit=100']); expect(http.unexpected).toEqual([]);
});

it.each([{ locale: 'en', mode: 'http', noMatch: false }, { locale: 'ru', mode: 'network', noMatch: true }] as const)(
  'PL.HTTP current server filters survive stale $mode with local no-match=$noMatch in $locale', async ({ locale, mode, noMatch }) => {
    const http = projectsHTTP(), initial = http.hold(); openProjects(locale); await finish(initial);
    const category = http.hold('?limit=100&category=fintech');
    const categoryButton = serverOption(locale, 'projectCategoryFilter', 'fintech'); await finish(category);
    const query = '?limit=100&category=fintech&status=building', status = http.hold(query);
    const statusButton = serverOption(locale, 'projectStatusFilter', 'building'); await finish(status);
    const timeButton = timeOption(locale, '7d'), sortButton = localOption(locale, 'votes');
    const search = screen.getByRole('textbox', { name: publicMessages[locale].search }), draft = noMatch ? 'Absent synthetic project' : 'Orbit';
    fireEvent.change(search, { target: { value: draft } });
    expect(http.requests).toHaveLength(3);
    const poll = http.hold(query); await act(async () => vi.advanceTimersByTimeAsync(30000));
    await act(async () => {
      if (mode === 'http') poll.resolve(Response.json({ detail: PRIVATE_FAILURE }, { status: 503 }));
      else poll.reject(new TypeError(PRIVATE_FAILURE));
    });
    expect(screen.getByRole('status').textContent).toContain(sharedMessages[locale].staleLastUpdated);
    expect(screen.getByRole('status').textContent).toContain('30'); expect(search).toHaveProperty('value', draft);
    for (const button of [categoryButton, statusButton, timeButton, sortButton]) expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(screen.queryByText(PRIVATE_FAILURE)).toBeNull();
    if (noMatch) expect(screen.getByText(publicMessages[locale].noProjectsFound)).toBeTruthy();
    else expect(projectIds()).toEqual(['runner', 'latest', 'middle']);
    const retry = http.hold(query); fireEvent.click(screen.getByRole('button', { name: sharedMessages[locale].retry })); await finish(retry);
    expect(screen.queryByRole('status')).toBeNull(); expect(search).toHaveProperty('value', draft);
    for (const button of [categoryButton, statusButton, timeButton, sortButton]) expect(button.getAttribute('aria-pressed')).toBe('true');
    fireEvent.change(search, { target: { value: 'Orbit' } }); expect(projectIds()).toEqual(['runner', 'latest', 'middle']);
    expect(http.requests).toEqual(['?limit=100', '?limit=100&category=fintech', query, query, query]); expect(http.unexpected).toEqual([]);
  });
