// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppRouterContext } from 'next/dist/shared/lib/app-router-context.shared-runtime';
import AgentsPage from './page';
import { API_URL, type Agent } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';
import { docsMessages } from '@/lib/i18n/docs';

vi.mock('@/components/Header', () => ({ Header: () => null }));
type Locale = 'en' | 'ru';
const NOW = '2026-10-20T12:00:00Z';
const PRIVATE_FAILURE = 'Synthetic internal leaderboard failure';
const router = { back: vi.fn(), forward: vi.fn(), refresh: vi.fn(), push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), bfcacheId: 'synthetic-route' };
const BASE: Agent = {
  id: 'old-high', name: 'Old high', handle: 'old-high', agent_type: 'external', model_provider: 'synthetic', model_name: 'synthetic',
  specialization: 'programmer', skills: [], karma: 900, projects_created: 0, code_commits: 0, reviews_done: 0,
  last_heartbeat: null, is_active: true, created_at: NOW, dna_risk: 5, dna_speed: 5, dna_verbosity: 5, dna_creativity: 5,
  bio: null, fork_count: 0, is_hosted: false,
};
const AGENTS: Agent[] = [BASE,
  { ...BASE, id: 'live-high', name: 'Recent high', karma: 800, is_active: false, last_heartbeat: '2026-10-20T11:00:00Z' },
  { ...BASE, id: 'live-low', name: 'Recent low', specialization: 'quant', karma: 100, last_heartbeat: '2026-10-20T10:00:00Z' },
  { ...BASE, id: 'old-low', name: 'Old low', karma: 1, last_heartbeat: '2026-10-18T12:00:00Z' },
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
function leaderboardHTTP() {
  const queue: Promise<Response>[] = [];
  const requests: string[] = [];
  const unexpected: string[] = [];
  const hold = () => { const pending = deferredResponse(); queue.push(pending.promise); return pending; };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL);
    const method = init?.method ?? 'GET';
    if (url.pathname !== '/api/v1/agents/leaderboard' || url.search !== '?limit=100' || method !== 'GET' || new Headers(init?.headers).has('Authorization')) {
      unexpected.push(`${method} ${url.pathname}${url.search}`); throw new Error('Unexpected synthetic request');
    }
    requests.push(url.pathname + url.search);
    const response = queue.shift();
    if (!response) { unexpected.push('Unplanned leaderboard request'); throw new Error('Unplanned synthetic request'); }
    return response;
  }));
  return { hold, requests, unexpected };
}
function openList(locale: Locale = 'en') {
  return render(<AppRouterContext.Provider value={router}><LocaleProvider initialLocale={locale}><AgentsPage /></LocaleProvider></AppRouterContext.Provider>);
}
async function finish(pending: ReturnType<typeof deferredResponse>, rows: Agent[]) {
  await act(async () => pending.resolve(Response.json(rows)));
}
function rowIds() {
  return [...screen.getByRole('main').querySelectorAll('a.agent-row')].map(row => row.getAttribute('href')?.split('/').at(-1));
}
function selectFilter(locale: Locale, active: boolean) {
  fireEvent.click(screen.getByRole('button', { name: publicMessages[locale][active ? 'filterActiveAgents' : 'filterAllAgents'] }));
}

it.each(['en', 'ru'] as const)('AL.HTTP held first read in %s becomes empty only after 200 []', async locale => {
  const http = leaderboardHTTP(), first = http.hold(); openList(locale);
  expect(screen.getByText(publicMessages[locale].loadingAgents)).toBeTruthy();
  expect(screen.queryByText(docsMessages[locale].eAgentsTitle)).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull(); expect(screen.queryByRole('textbox')).toBeNull();
  expect(http.requests).toHaveLength(1);
  await finish(first, []);
  expect(screen.queryByText(publicMessages[locale].loadingAgents)).toBeNull();
  expect(screen.getByText(docsMessages[locale].eAgentsTitle)).toBeTruthy();
  expect(screen.queryByRole('textbox')).toBeNull(); expect(http.unexpected).toEqual([]);
});

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }] as const)(
  'AL.HTTP first $mode error in $locale stays generic and Retry recovers', async ({ locale, mode }) => {
    const http = leaderboardHTTP(), first = http.hold(); openList(locale);
    await act(async () => {
      if (mode === 'http') first.resolve(Response.json({ detail: PRIVATE_FAILURE }, { status: 503 }));
      else first.reject(new TypeError(PRIVATE_FAILURE));
    });
    expect(screen.getByRole('alert').textContent).toContain(sharedMessages[locale].couldNotLoadData);
    expect(screen.queryByText(PRIVATE_FAILURE)).toBeNull();
    expect(screen.queryByText(docsMessages[locale].eAgentsTitle)).toBeNull();
    const next = http.hold(); fireEvent.click(screen.getByRole('button', { name: sharedMessages[locale].retry }));
    await finish(next, AGENTS);
    expect(rowIds()).toEqual(['live-high', 'live-low', 'old-high', 'old-low']);
    expect(screen.queryByRole('alert')).toBeNull(); expect(http.requests).toHaveLength(2); expect(http.unexpected).toEqual([]);
  });

it('AL.HTTP local name/specialization search and heartbeat filter preserve stable live-first order', async () => {
  const http = leaderboardHTTP(), first = http.hold(); openList(); await finish(first, AGENTS);
  expect(rowIds()).toEqual(['live-high', 'live-low', 'old-high', 'old-low']);
  selectFilter('en', true); expect(rowIds()).toEqual(['live-high', 'live-low']);
  expect(screen.getByRole('button', { name: publicMessages.en.filterActiveAgents }).getAttribute('aria-pressed')).toBe('true');
  const search = screen.getByRole('textbox', { name: publicMessages.en.search });
  fireEvent.change(search, { target: { value: 'QUANT' } }); expect(rowIds()).toEqual(['live-low']);
  fireEvent.change(search, { target: { value: 'RECENT HIGH' } }); expect(rowIds()).toEqual(['live-high']);
  selectFilter('en', false); fireEvent.change(search, { target: { value: '' } });
  expect(rowIds()).toEqual(['live-high', 'live-low', 'old-high', 'old-low']);
  expect(http.requests).toHaveLength(1); expect(http.unexpected).toEqual([]);
});

it.each([false, true])('AL.HTTP stale poll preserves current filter/search and no-match=%s', async noMatch => {
  const http = leaderboardHTTP(), first = http.hold(); openList(); await finish(first, AGENTS);
  selectFilter('en', true); const search = screen.getByRole('textbox', { name: publicMessages.en.search });
  const draft = noMatch ? 'Absent synthetic name' : 'RECENT HIGH';
  fireEvent.change(search, { target: { value: draft } });
  const poll = http.hold(); await act(async () => vi.advanceTimersByTimeAsync(30000));
  expect(http.requests).toHaveLength(2);
  await act(async () => poll.resolve(Response.json({ detail: PRIVATE_FAILURE }, { status: 503 })));
  expect(screen.getByRole('status').textContent).toContain('30s ago');
  expect(screen.getByRole('status').textContent).toContain(sharedMessages.en.staleLastUpdated);
  expect(search).toHaveProperty('value', draft);
  expect(screen.getByRole('button', { name: publicMessages.en.filterActiveAgents }).getAttribute('aria-pressed')).toBe('true');
  expect(screen.queryByText(PRIVATE_FAILURE)).toBeNull();
  if (noMatch) expect(screen.getByText(publicMessages.en.noAgentsMatchYourFilter)).toBeTruthy();
  else expect(rowIds()).toEqual(['live-high']);
  const retry = http.hold(); fireEvent.click(screen.getByRole('button', { name: sharedMessages.en.retry }));
  await finish(retry, AGENTS);
  expect(screen.queryByRole('status')).toBeNull(); expect(search).toHaveProperty('value', draft);
  if (noMatch) expect(screen.getByText(publicMessages.en.noAgentsMatchYourFilter)).toBeTruthy();
  else expect(rowIds()).toEqual(['live-high']);
  expect(http.requests).toHaveLength(3); expect(http.unexpected).toEqual([]);
});
