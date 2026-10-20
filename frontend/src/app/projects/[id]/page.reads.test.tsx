// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProjectPage from './page';
import { API_URL, type ProjectOwnership } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';

const route = vi.hoisted(() => ({ id: 'synthetic-a' }));
vi.mock('next/navigation', () => ({ useParams: () => route, usePathname: () => `/projects/${route.id}`, useRouter: () => ({ refresh: vi.fn() }) }));
type Locale = 'en' | 'ru';
type Section = 'ownership' | 'contributors';
type HumanContributor = { id: string; role: string; contribution_points: number; joined_at: string;
  user_id: string; user_name: string; user_email: string; wallet_address: string | null };
const NOW = '2026-10-20T12:00:00Z';
const INTERNAL = 'Synthetic internal project read failure';
const PROJECT = { id: 'synthetic-a', title: 'Synthetic project A', description: 'Synthetic project overview', category: 'tools',
  status: 'active', repo_url: null, deploy_url: null, tech_stack: ['TypeScript'], agent_name: 'Synthetic agent',
  agent_handle: 'synthetic-agent', creator_agent_id: 'synthetic-agent', votes_up: 0, votes_down: 0, created_at: NOW };
const OWNERSHIP: ProjectOwnership = { project_id: PROJECT.id, project_title: PROJECT.title, contributors: [{
  agent_id: 'synthetic-agent', agent_name: 'Synthetic owner A', owner_user_id: null, owner_name: null,
  wallet_address: null, contribution_points: 10, share_pct: 100,
}] };
function contributors(name = 'Synthetic member A'): { contributors: HumanContributor[]; total: number } {
  return { contributors: [{ id: 'synthetic-membership', role: 'contributor', contribution_points: 3, joined_at: NOW,
    user_id: 'synthetic-user', user_name: name, user_email: 'synthetic@example.invalid', wallet_address: null }], total: 1 };
}
const path = (section?: Section, id = PROJECT.id) => `/api/v1/projects/${id}${section ? `/${section}` : ''}`;
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function projectHTTP() {
  const plans = new Map<string, Promise<Response>[]>(), bodies = new Map<string, unknown>();
  const calls: { path: string; authorization: string | null }[] = [], unexpected: string[] = [];
  for (const id of [PROJECT.id, 'synthetic-b']) {
    bodies.set(path(undefined, id), { ...PROJECT, id, title: id === PROJECT.id ? PROJECT.title : 'Synthetic project B' });
    bodies.set(path('ownership', id), { ...OWNERSHIP, project_id: id });
    bodies.set(path('contributors', id), contributors(id === PROJECT.id ? 'Synthetic member A' : 'Synthetic member B'));
  }
  bodies.set('/api/v1/auth/me', { id: 'synthetic-user', name: 'Synthetic account' });
  const hold = (url: string) => {
    const pending = deferred<Response>(); plans.set(url, [...(plans.get(url) ?? []), pending.promise]); return pending;
  };
  const holdBody = (url: string) => {
    const pending = deferred<unknown>(), response = Response.json(null); response.json = () => pending.promise;
    plans.set(url, [...(plans.get(url) ?? []), Promise.resolve(response)]); return pending;
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL), key = url.pathname + url.search;
    const authorization = new Headers(init?.headers).get('Authorization'); calls.push({ path: key, authorization });
    if (!bodies.has(key) || (init?.method ?? 'GET') !== 'GET') { unexpected.push(key); throw new Error('Unexpected synthetic project request'); }
    return plans.get(key)?.shift() ?? Response.json(bodies.get(key));
  }));
  return { calls, unexpected, bodies, hold, holdBody };
}
const settle = async () => { await act(async () => {}); };
async function finish<T>(pending: ReturnType<typeof deferred<T>>, value: T) { await act(async () => pending.resolve(value)); }
function openProject(locale: Locale = 'en') {
  const tree = () => <LocaleProvider initialLocale={locale}><ProjectPage /></LocaleProvider>;
  const view = render(tree());
  return { ...view, navigate: async () => { route.id = 'synthetic-b'; view.rerender(tree()); await settle(); } };
}
const main = () => within(screen.getByRole('main'));
function select(section: Section | 'overview', locale: Locale = 'en') {
  const label = section === 'contributors' ? publicMessages[locale].contributors : publicMessages[locale][section];
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${label}`) }));
}
function counter(locale: Locale = 'en') {
  const container = main().getByText(publicMessages[locale].contributors, { exact: true }).parentElement;
  if (!(container instanceof HTMLElement)) throw new Error('Missing contributors counter');
  return within(container);
}
const retry = (locale: Locale = 'en') => main().getByRole('button', { name: sharedMessages[locale].retry });
const reads = (http: ReturnType<typeof projectHTTP>, key: string) => http.calls.filter(call => call.path === key);
function auth(token: string) {
  localStorage.setItem('auth', JSON.stringify({ token, userId: 'synthetic-user', email: 'synthetic@example.invalid' }));
  window.dispatchEvent(new Event('storage'));
}
beforeEach(() => { route.id = PROJECT.id; localStorage.clear(); vi.useFakeTimers(); vi.setSystemTime(new Date(NOW)); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('PD.R1 held primary becomes genuine404 without a generic error or Retry', async () => {
  const http = projectHTTP(), pending = http.hold(path()); openProject(); await settle();
  expect(main().getByText(publicMessages.en.loading)).toBeTruthy(); expect(screen.queryByText(publicMessages.en.projectNotFound)).toBeNull();
  await finish(pending, Response.json({ detail: INTERNAL }, { status: 404 }));
  expect(main().getByText(publicMessages.en.projectNotFound)).toBeTruthy(); expect(main().queryByRole('alert')).toBeNull();
  expect(main().queryByRole('button', { name: sharedMessages.en.retry })).toBeNull(); expect(http.unexpected).toEqual([]);
});

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }, { locale: 'en', mode: 'null' },
  { locale: 'en', mode: 'identity' }, { locale: 'ru', mode: 'title' }, { locale: 'en', mode: 'tech' }] as const)(
  'PD.R1 $locale primary $mode is generic unavailable and exact singleflight Retry recovers', async ({ locale, mode }) => {
    const http = projectHTTP(), pending = http.hold(path()); openProject(locale); await settle();
    await act(async () => {
      if (mode === 'network') pending.reject(new TypeError(INTERNAL));
      else {
        const invalid = mode === 'null' ? null : mode === 'identity' ? { ...PROJECT, id: 'different-project' }
          : mode === 'title' ? { ...PROJECT, title: 42 } : { ...PROJECT, tech_stack: 'invalid-array' };
        pending.resolve(Response.json(mode === 'http' ? { detail: INTERNAL } : invalid, { status: mode === 'http' ? 503 : 200 }));
      }
    });
    expect(main().getByRole('alert').textContent).toContain(sharedMessages[locale].couldNotLoadData);
    expect(screen.queryByText(publicMessages[locale].projectNotFound)).toBeNull(); expect(screen.queryByText(INTERNAL)).toBeNull();
    const next = http.hold(path()), button = retry(locale); act(() => { button.click(); button.click(); });
    expect(reads(http, path())).toHaveLength(2); await finish(next, Response.json(PROJECT));
    expect(screen.getByRole('heading', { name: PROJECT.title })).toBeTruthy();
    expect(reads(http, path('ownership'))).toHaveLength(1); expect(http.unexpected).toEqual([]);
  });

it('PD.R2 held ownership keeps overview usable and shows pending only in its section', async () => {
  const http = projectHTTP(), pending = http.hold(path('ownership')); openProject(); await settle();
  expect(screen.getByRole('heading', { name: PROJECT.title })).toBeTruthy(); expect(main().getByText(PROJECT.description)).toBeTruthy();
  select('ownership'); expect(main().getByRole('status')).toBeTruthy(); expect(main().queryByText(publicMessages.en.noOwnershipDataAvailable)).toBeNull();
  await finish(pending, Response.json(OWNERSHIP)); expect(main().getByText('Synthetic owner A')).toBeTruthy(); expect(http.unexpected).toEqual([]);
});

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }, { locale: 'en', mode: 'null' }, { locale: 'ru', mode: 'array' }] as const)(
  'PD.R2 $locale ownership $mode preserves primary and retries only ownership', async ({ locale, mode }) => {
    const http = projectHTTP(), pending = http.hold(path('ownership')); openProject(locale);
    await act(async () => mode === 'network' ? pending.reject(new TypeError(INTERNAL)) : pending.resolve(Response.json(
      mode === 'http' ? { detail: INTERNAL } : mode === 'null' ? null : { ...OWNERSHIP, contributors: {} }, { status: mode === 'http' ? 503 : 200 })));
    expect(screen.getByRole('heading', { name: PROJECT.title })).toBeTruthy(); select('ownership', locale);
    expect(main().getByRole('alert').textContent).toContain(sharedMessages[locale].couldNotLoadData);
    expect(main().queryByText(publicMessages[locale].noAgentContributorsYet)).toBeNull(); expect(screen.queryByText(INTERNAL)).toBeNull();
    const next = http.hold(path('ownership')), button = retry(locale); act(() => { button.click(); button.click(); });
    expect(reads(http, path('ownership'))).toHaveLength(2); await finish(next, Response.json(OWNERSHIP));
    expect(main().getByText('Synthetic owner A')).toBeTruthy(); expect(reads(http, path())).toHaveLength(1); expect(http.unexpected).toEqual([]);
  });

it.each(['en', 'ru'] as const)('PD.R3 %s contributors held and failed is unknown, then Retry confirms empty zero', async locale => {
  const http = projectHTTP(), pending = http.hold(path('contributors')); openProject(locale); await settle();
  expect(counter(locale).queryByText('0')).toBeNull(); select('contributors', locale);
  expect(main().getByRole('status')).toBeTruthy(); expect(main().queryByText(new RegExp(publicMessages[locale].noContributorsYet))).toBeNull();
  await finish(pending, Response.json({ detail: INTERNAL }, { status: 503 }));
  expect(main().getByRole('alert').textContent).toContain(sharedMessages[locale].couldNotLoadData);
  expect(main().queryByText(new RegExp(publicMessages[locale].noContributorsYet))).toBeNull();
  http.bodies.set(path('contributors'), { contributors: [], total: 0 }); fireEvent.click(retry(locale)); await settle();
  expect(main().getByText(new RegExp(publicMessages[locale].noContributorsYet))).toBeTruthy(); select('overview', locale);
  expect(counter(locale).getByText('0')).toBeTruthy(); expect(reads(http, path('contributors'))).toHaveLength(2); expect(http.unexpected).toEqual([]);
});

it.each(['network', 'array'] as const)('PD.R3 contributors %s failure cannot pretend empty and exact Retry restores populated rows', async mode => {
  const http = projectHTTP(), pending = http.hold(path('contributors')); openProject(); await settle();
  await act(async () => mode === 'network' ? pending.reject(new TypeError(INTERNAL)) : pending.resolve(Response.json({ contributors: {} })));
  select('contributors'); expect(main().getByRole('alert')).toBeTruthy(); expect(screen.queryByText(INTERNAL)).toBeNull();
  const next = http.hold(path('contributors')), button = retry(); act(() => { button.click(); button.click(); });
  expect(reads(http, path('contributors'))).toHaveLength(2); await finish(next, Response.json(contributors()));
  expect(main().getByText('Synthetic member A')).toBeTruthy(); select('overview'); expect(counter().getByText('1')).toBeTruthy(); expect(http.unexpected).toEqual([]);
});

it('PD.R3 auth refresh failure keeps confirmed contributor rows/count and Retry uses the current Bearer', async () => {
  const http = projectHTTP(); auth('synthetic-token-a'); openProject(); await settle();
  expect(counter().getByText('1')).toBeTruthy(); const pending = http.hold(path('contributors'));
  await act(async () => auth('synthetic-token-b')); await finish(pending, Response.json({ detail: INTERNAL }, { status: 503 }));
  expect(counter().getByText('1')).toBeTruthy(); select('contributors'); expect(main().getByText('Synthetic member A')).toBeTruthy(); expect(main().getByRole('alert')).toBeTruthy();
  http.bodies.set(path('contributors'), contributors('Synthetic refreshed member')); fireEvent.click(retry()); await settle();
  expect(main().getByText('Synthetic refreshed member')).toBeTruthy();
  expect(reads(http, path('contributors')).map(call => call.authorization)).toEqual(['Bearer synthetic-token-a', 'Bearer synthetic-token-b', 'Bearer synthetic-token-b']);
  expect(reads(http, path()).every(call => call.authorization === null)).toBe(true); expect(http.unexpected).toEqual([]);
});

it.each(['en', 'ru'] as const)('PD.R5 %s populated and successful empty sections are local with explicit selected state', async locale => {
  const http = projectHTTP(); http.bodies.set(path('ownership'), { ...OWNERSHIP, contributors: [] }); openProject(locale); await settle();
  expect(counter(locale).getByText('1')).toBeTruthy(); const before = http.calls.length;
  select('ownership', locale); expect(main().getByText(publicMessages[locale].noAgentContributorsYet)).toBeTruthy();
  select('contributors', locale); expect(main().getByText('Synthetic member A')).toBeTruthy(); select('overview', locale);
  expect(main().getByText(PROJECT.description)).toBeTruthy(); expect(http.calls).toHaveLength(before);
  expect(screen.getByRole('button', { name: publicMessages[locale].overview }).getAttribute('aria-pressed')).toBe('true'); expect(http.unexpected).toEqual([]);
});

it('PD.R4 old contributor body cannot clear a newer account read pending or replace its result', async () => {
  const http = projectHTTP(), old = http.holdBody(path('contributors')); auth('synthetic-token-a'); openProject(); await settle();
  const next = http.hold(path('contributors')); await act(async () => auth('synthetic-token-b')); select('contributors');
  await finish(old, contributors('Synthetic stale member'));
  expect(main().queryByText('Synthetic stale member')).toBeNull(); expect(main().getByRole('status')).toBeTruthy();
  await finish(next, Response.json(contributors('Synthetic current member'))); expect(main().getByText('Synthetic current member')).toBeTruthy();
  expect(main().queryByRole('status')).toBeNull(); expect(http.unexpected).toEqual([]);
});

it.each(['ownership', 'contributors'] as const)('PD.R4 old %s JSON after route replacement cannot mutate current data', async section => {
  const http = projectHTTP(), old = http.holdBody(path(section)), view = openProject(); await settle(); await view.navigate();
  select(section); await finish(old, section === 'ownership' ? { ...OWNERSHIP, contributors: [{ ...OWNERSHIP.contributors[0], agent_name: 'Stale owner' }] } : contributors('Stale member'));
  expect(screen.getByRole('heading', { name: 'Synthetic project B' })).toBeTruthy(); expect(screen.queryByText(/Stale owner|Stale member/)).toBeNull(); expect(http.unexpected).toEqual([]);
});

it('PD.R4 unmounted deferred auxiliary body cannot trigger further reads', async () => {
  const http = projectHTTP(), old = http.holdBody(path('contributors')), view = openProject(); await settle(); view.unmount();
  const before = http.calls.length; await finish(old, contributors()); expect(http.calls).toHaveLength(before); expect(screen.queryByRole('main')).toBeNull(); expect(http.unexpected).toEqual([]);
});
