// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TeamPage from './page';
import { API_URL, type TeamDetail } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';

const route = vi.hoisted(() => ({ id: 'synthetic-team-a' }));
vi.mock('next/navigation', () => ({ useParams: () => route, usePathname: () => `/teams/${route.id}`, useRouter: () => ({ refresh: vi.fn() }) }));
type Locale = 'en' | 'ru';
type TeamWire = Omit<TeamDetail, 'member_count' | 'project_count'>;
const NOW = '2026-10-20T12:00:00Z', INTERNAL = 'Synthetic internal team read failure';
const path = (id = 'synthetic-team-a') => `/api/v1/teams/${id}`;
function team(id = 'synthetic-team-a'): TeamWire {
  const suffix = id === 'synthetic-team-a' ? 'A' : 'B';
  return { id, name: `Synthetic Team ${suffix}`, description: 'Synthetic team overview', avatar_url: null, creator_name: `Synthetic Owner ${suffix}`, created_at: NOW,
    members: [{ id: `synthetic-membership-${suffix}`, agent_id: `synthetic-agent-${suffix}`, user_id: null, name: `Synthetic Owner ${suffix}`,
      handle: `synthetic-owner-${suffix}`, role: 'owner', member_type: 'agent', joined_at: NOW }],
    projects: [{ id: `synthetic-project-${suffix}`, title: `Synthetic Project ${suffix}`, description: 'Synthetic linked project', status: 'active', repo_url: null, deploy_url: null, agent_name: 'Synthetic agent' }] };
}
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject };
}
function teamHTTP() {
  const bodies = new Map<string, unknown>(), plans = new Map<string, Promise<Response>[]>();
  const calls: { path: string; method: string; authorization: string | null }[] = [], unexpected: string[] = [];
  const streams: { url: string; closed: boolean }[] = [];
  for (const id of ['synthetic-team-a', 'synthetic-team-b']) { bodies.set(path(id), team(id)); bodies.set(`${path(id)}/messages?limit=50`, []); }
  bodies.set('/api/v1/auth/me', { detail: 'Synthetic anonymous request' });
  const hold = (key = path()) => { const pending = deferred<Response>(); plans.set(key, [...(plans.get(key) ?? []), pending.promise]); return pending; };
  const holdBody = () => {
    const pending = deferred<unknown>(), response = Response.json(null); response.json = () => pending.promise;
    plans.set(path(), [...(plans.get(path()) ?? []), Promise.resolve(response)]); return pending;
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL), key = url.pathname + url.search, method = init?.method ?? 'GET';
    calls.push({ path: key, method, authorization: new Headers(init?.headers).get('Authorization') });
    if (method !== 'GET' || !bodies.has(key)) { unexpected.push(`${method} ${key}`); throw new Error('Unexpected synthetic team request'); }
    return plans.get(key)?.shift() ?? Response.json(bodies.get(key), { status: key === '/api/v1/auth/me' ? 401 : 200 });
  }));
  vi.stubGlobal('EventSource', class {
    record: { url: string; closed: boolean };
    constructor(url: string) {
      if (!['synthetic-team-a', 'synthetic-team-b'].some(id => url === `${API_URL}${path(id)}/stream`)) { unexpected.push(`SSE ${url}`); throw new Error('Unexpected synthetic team stream'); }
      this.record = { url, closed: false }; streams.push(this.record);
    }
    close() { this.record.closed = true; }
  });
  return { calls, unexpected, streams, hold, holdBody };
}
const main = () => within(screen.getByRole('main'));
const settle = async () => { await act(async () => {}); };
async function finish<T>(pending: ReturnType<typeof deferred<T>>, value: T) { await act(async () => pending.resolve(value)); }
const reads = (http: ReturnType<typeof teamHTTP>, id = 'synthetic-team-a') => http.calls.filter(call => call.path === path(id));
function openTeam(locale: Locale = 'en') {
  const tree = () => <LocaleProvider initialLocale={locale}><TeamPage /></LocaleProvider>, view = render(tree());
  return { ...view, navigate: async () => { route.id = 'synthetic-team-b'; view.rerender(tree()); await settle(); } };
}
let scrollDescriptor: PropertyDescriptor | undefined;
beforeEach(() => {
  route.id = 'synthetic-team-a'; localStorage.clear(); vi.useFakeTimers(); vi.setSystemTime(new Date(NOW));
  scrollDescriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView');
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, writable: true, value: vi.fn() });
});
afterEach(() => {
  cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers();
  if (scrollDescriptor) Object.defineProperty(Element.prototype, 'scrollIntoView', scrollDescriptor); else Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
});

it('TD.R1 held anonymous detail distinguishes pending from confirmed data', async () => {
  const http = teamHTTP(), pending = http.hold(); openTeam(); await settle();
  expect(main().getByText(publicMessages.en.loadingTeam)).toBeTruthy(); expect(main().queryByText(publicMessages.en.teamNotFound)).toBeNull();
  expect(http.streams).toHaveLength(0); expect(http.calls.some(call => call.path.includes('/messages?'))).toBe(false);
  await finish(pending, Response.json(team())); expect(main().getByRole('heading', { name: team().name })).toBeTruthy();
  expect(reads(http)).toEqual([{ path: path(), method: 'GET', authorization: null }]); expect(http.streams).toHaveLength(1); expect(http.unexpected).toEqual([]);
});
it('TD.R1 genuine404 keeps the existing not-found route and Back without Retry', async () => {
  const http = teamHTTP(), pending = http.hold(); openTeam('ru'); await settle(); await finish(pending, Response.json({ detail: INTERNAL }, { status: 404 }));
  expect(main().getByText(publicMessages.ru.teamNotFound)).toBeTruthy(); expect(main().getByRole('link', { name: publicMessages.ru.backToTeams }).getAttribute('href')).toBe('/teams');
  expect(main().queryByRole('alert')).toBeNull(); expect(main().queryByRole('button', { name: sharedMessages.ru.retry })).toBeNull();
  expect(http.streams).toHaveLength(0); expect(screen.queryByText(INTERNAL)).toBeNull(); expect(http.unexpected).toEqual([]);
});
it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }, { locale: 'en', mode: 'json' }, { locale: 'ru', mode: 'malformed' }] as const)(
  'TD.R1 $locale $mode is unavailable and explicit synchronous Retry owns one fresh GET', async ({ locale, mode }) => {
    const http = teamHTTP(), pending = http.hold(); openTeam(locale); await settle();
    await act(async () => {
      if (mode === 'network') pending.reject(new TypeError(INTERNAL));
      else if (mode === 'json') pending.resolve(new Response('{', { status: 200 }));
      else pending.resolve(Response.json(mode === 'http' ? { detail: INTERNAL } : { ...team(), members: null }, { status: mode === 'http' ? 503 : 200 }));
    });
    expect(main().getByRole('alert').textContent).toContain(sharedMessages[locale].couldNotLoadData);
    expect(main().queryByText(publicMessages[locale].teamNotFound)).toBeNull(); expect(screen.queryByText(INTERNAL)).toBeNull(); expect(http.streams).toHaveLength(0);
    const next = http.hold(), retry = main().getByRole('button', { name: sharedMessages[locale].retry }); act(() => { retry.click(); retry.click(); });
    expect(reads(http)).toHaveLength(2); await finish(next, Response.json(team()));
    expect(main().getByRole('heading', { name: team().name })).toBeTruthy(); expect(main().queryByRole('alert')).toBeNull(); expect(http.unexpected).toEqual([]);
  });
it.each(['response', 'body', 'error'] as const)('TD.R3 late A %s cannot replace ready B or reopen old read resources', async mode => {
  const http = teamHTTP(), oldBody = mode === 'body' ? http.holdBody() : null, old = mode === 'body' ? null : http.hold(), view = openTeam();
  await settle(); await view.navigate(); expect(main().getByRole('heading', { name: team('synthetic-team-b').name })).toBeTruthy();
  const before = http.calls.length, streamCount = http.streams.length;
  if (oldBody) await finish(oldBody, team());
  if (old) await act(async () => mode === 'error' ? old.reject(new TypeError(INTERNAL)) : old.resolve(Response.json(team())));
  expect(main().getByRole('heading', { name: team('synthetic-team-b').name })).toBeTruthy(); expect(main().queryByText(publicMessages.en.teamNotFound)).toBeNull();
  expect(http.calls).toHaveLength(before); expect(http.streams).toHaveLength(streamCount); expect(http.unexpected).toEqual([]);
});
it('TD.R3 route B starts pending after A404 and can recover without the old not-found state', async () => {
  const http = teamHTTP(), old = http.hold(), view = openTeam(); await settle(); await finish(old, Response.json({}, { status: 404 }));
  const current = http.hold(path('synthetic-team-b')); await view.navigate();
  expect(main().getByText(publicMessages.en.loadingTeam)).toBeTruthy(); expect(main().queryByText(publicMessages.en.teamNotFound)).toBeNull();
  await finish(current, Response.json(team('synthetic-team-b'))); expect(main().getByRole('heading', { name: team('synthetic-team-b').name })).toBeTruthy(); expect(http.unexpected).toEqual([]);
});
it('TD.R3 unmounted held JSON cannot start history or a stream', async () => {
  const http = teamHTTP(), body = http.holdBody(), view = openTeam(); await settle(); view.unmount(); const before = http.calls.length;
  await finish(body, team()); expect(http.calls).toHaveLength(before); expect(http.streams).toHaveLength(0); expect(http.unexpected).toEqual([]);
});
it('TD.R4 existing member/project links and local sections do not repeat the detail GET', async () => {
  const http = teamHTTP(); openTeam(); await settle();
  expect(main().getByRole('link', { name: /Synthetic Owner A/ }).getAttribute('href')).toBe('/agents/synthetic-agent-A');
  fireEvent.click(main().getByRole('button', { name: `${publicMessages.en.projects} (1)` }));
  expect(main().getByRole('link', { name: /Synthetic Project A/ }).getAttribute('href')).toBe('/projects/synthetic-project-A');
  fireEvent.click(main().getByRole('button', { name: `${publicMessages.en.members} (1)` })); expect(main().getByRole('link', { name: /Synthetic Owner A/ })).toBeTruthy();
  expect(reads(http)).toHaveLength(1); expect(http.calls.every(call => call.method === 'GET')).toBe(true); expect(http.unexpected).toEqual([]);
});
