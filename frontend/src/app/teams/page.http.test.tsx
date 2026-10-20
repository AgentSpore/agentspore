// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppRouterContext } from 'next/dist/shared/lib/app-router-context.shared-runtime';
import TeamsPage from './page';
import { API_URL, type Team } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { docsMessages } from '@/lib/i18n/docs';
import { sharedMessages } from '@/lib/i18n/shared';

vi.mock('@/components/Header', () => ({ Header: () => null }));
type Locale = 'en' | 'ru';
type Failure = 'http' | 'network';
const NOW = '2026-10-20T12:00:00Z';
const PRIVATE_FAILURE = 'Synthetic internal team read failure';
const router = { back: vi.fn(), forward: vi.fn(), refresh: vi.fn(), push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), bfcacheId: 'synthetic-route' };
const TEAM: Team = { id: 'synthetic-team', name: 'Synthetic team', description: 'Synthetic team description',
  avatar_url: null, creator_name: 'Synthetic creator', member_count: 3, project_count: 2, created_at: NOW };
const TEAMS: Team[] = [TEAM, { ...TEAM, id: 'second-team', name: 'Second team', creator_name: 'Second creator' }];
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
function teamsHTTP() {
  const queue: Promise<Response>[] = [], requests: string[] = [], unexpected: string[] = [];
  const hold = () => { const pending = deferredResponse(); queue.push(pending.promise); return pending; };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL);
    if (url.pathname !== '/api/v1/teams' || url.search !== '?limit=50' || (init?.method ?? 'GET') !== 'GET' || new Headers(init?.headers).has('Authorization')) {
      unexpected.push(url.pathname + url.search); throw new Error('Unexpected synthetic request');
    }
    requests.push(url.pathname + url.search);
    const response = queue.shift();
    if (!response) { unexpected.push('Unplanned teams request'); throw new Error('Unplanned synthetic request'); }
    return response;
  }));
  return { hold, requests, unexpected };
}
function openTeams(locale: Locale = 'en') {
  return render(<AppRouterContext.Provider value={router}><LocaleProvider initialLocale={locale}><TeamsPage /></LocaleProvider></AppRouterContext.Provider>);
}
async function finish(pending: ReturnType<typeof deferredResponse>, teams: Team[]) {
  await act(async () => pending.resolve(Response.json(teams)));
}
async function fail(pending: ReturnType<typeof deferredResponse>, mode: Failure) {
  await act(async () => {
    if (mode === 'http') pending.resolve(Response.json({ detail: PRIVATE_FAILURE }, { status: 503 }));
    else pending.reject(new TypeError(PRIVATE_FAILURE));
  });
}
function cardIds() {
  return [...screen.getByRole('main').querySelectorAll('a.team-card')].map(card => card.getAttribute('href')?.split('/').at(-1));
}

it.each(['en', 'ru'] as const)('TL.HTTP pending in %s never claims an unknown total or successful empty', async locale => {
  const http = teamsHTTP(), initial = http.hold(); openTeams(locale);
  expect(screen.getByText(publicMessages[locale].loadingTeams)).toBeTruthy();
  expect(screen.queryByText(publicMessages[locale].total2)).toBeNull();
  expect(screen.queryByText(docsMessages[locale].eTeamsTitle)).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull(); expect(cardIds()).toEqual([]);
  await finish(initial, []);
  expect(screen.queryByText(publicMessages[locale].loadingTeams)).toBeNull();
  expect(screen.getByText(docsMessages[locale].eTeamsTitle)).toBeTruthy();
  expect(screen.getByText(publicMessages[locale].total2).parentElement?.textContent).toContain('0');
  expect(http.requests).toHaveLength(1); expect(http.unexpected).toEqual([]);
});

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }] as const)(
  'TL.HTTP first $mode failure in $locale has one generic Retry instead of empty or zero', async ({ locale, mode }) => {
    const http = teamsHTTP(), initial = http.hold(); openTeams(locale); await fail(initial, mode);
    expect(screen.getByRole('alert').textContent).toContain(sharedMessages[locale].couldNotLoadData);
    expect(screen.queryByText(docsMessages[locale].eTeamsTitle)).toBeNull();
    expect(screen.queryByText(publicMessages[locale].total2)).toBeNull();
    expect(screen.queryByRole('status')).toBeNull(); expect(screen.queryByText(PRIVATE_FAILURE)).toBeNull();
    expect(screen.getAllByRole('button', { name: sharedMessages[locale].retry })).toHaveLength(1);
    const next = http.hold(); fireEvent.click(screen.getByRole('button', { name: sharedMessages[locale].retry }));
    await finish(next, TEAMS);
    expect(cardIds()).toEqual(['synthetic-team', 'second-team']); expect(screen.queryByRole('alert')).toBeNull();
    expect(http.requests).toHaveLength(2); expect(http.unexpected).toEqual([]);
  });

it('TL.HTTP successful populated response preserves team fields and navigation', async () => {
  const http = teamsHTTP(), initial = http.hold(); openTeams(); await finish(initial, TEAMS);
  expect(cardIds()).toEqual(['synthetic-team', 'second-team']);
  const card = screen.getByRole('heading', { name: TEAM.name }).closest('a');
  expect(card?.getAttribute('href')).toBe('/teams/synthetic-team');
  expect(card?.textContent).toContain(TEAM.creator_name); expect(card?.textContent).toContain(TEAM.description);
  expect(screen.getByText(publicMessages.en.total2).parentElement?.textContent).toContain('2');
  expect(http.unexpected).toEqual([]);
});

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }] as const)(
  'TL.HTTP stale $mode poll in $locale retains cards and last success until Retry recovers', async ({ locale, mode }) => {
    const http = teamsHTTP(), initial = http.hold(); openTeams(locale); await finish(initial, TEAMS);
    const poll = http.hold(); await act(async () => vi.advanceTimersByTimeAsync(30000)); await fail(poll, mode);
    expect(cardIds()).toEqual(['synthetic-team', 'second-team']);
    const status = screen.getByRole('status');
    expect(status.textContent).toContain(sharedMessages[locale].staleLastUpdated); expect(status.textContent).toContain('30');
    expect(screen.queryByRole('alert')).toBeNull(); expect(screen.queryByText(docsMessages[locale].eTeamsTitle)).toBeNull();
    expect(screen.queryByText(PRIVATE_FAILURE)).toBeNull();
    expect(screen.getByText(publicMessages[locale].total2).parentElement?.textContent).toContain('2');
    const next = http.hold(); fireEvent.click(screen.getByRole('button', { name: sharedMessages[locale].retry }));
    await finish(next, [{ ...TEAM, id: 'recovered-team', name: 'Recovered team' }]);
    expect(cardIds()).toEqual(['recovered-team']); expect(screen.queryByRole('status')).toBeNull();
    expect(http.requests).toHaveLength(3); expect(http.unexpected).toEqual([]);
  });
