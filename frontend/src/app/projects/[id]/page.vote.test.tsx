// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProjectPage from './page';
import { API_URL, type ProjectOwnership } from '@/lib/api';
import { type Project } from './useProjectReads';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';

const route = vi.hoisted(() => ({ id: 'synthetic-a' }));
vi.mock('next/navigation', () => ({ useParams: () => route, usePathname: () => `/projects/${route.id}`, useRouter: () => ({ refresh: vi.fn() }) }));
type Locale = 'en' | 'ru';
type Direction = 1 | -1;
const NOW = '2026-10-20T12:00:00Z';
const INTERNAL = 'Synthetic internal vote failure';
const COPY = {
  en: { error: 'Could not submit your vote. Try again.', wait: 'Please wait before voting again.' },
  ru: { error: 'Не удалось отправить голос. Попробуйте ещё раз.', wait: 'Подождите перед повторным голосованием.' },
};
const PROJECT: Project = { id: 'synthetic-a', title: 'Synthetic project A', description: 'Synthetic overview', category: 'tools',
  status: 'active', repo_url: null, deploy_url: null, tech_stack: [], agent_name: 'Synthetic agent', agent_handle: 'synthetic-agent',
  creator_agent_id: 'synthetic-agent', votes_up: 12, votes_down: 2, created_at: NOW };
const projectPath = (id = PROJECT.id) => `/api/v1/projects/${id}`;
const votePath = (id = PROJECT.id) => `${projectPath(id)}/vote`;
const canonical = (up = 19, down = 4, id = PROJECT.id) => ({ project_id: id, votes_up: up, votes_down: down, score: up - down });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function voteHTTP() {
  const bodies = new Map<string, unknown>(), plans = new Map<string, Promise<Response>[]>();
  const calls: { path: string; method: string; body: unknown; authorization: string | null; contentType: string | null }[] = [], unexpected: string[] = [];
  for (const id of [PROJECT.id, 'synthetic-b']) {
    const project = id === PROJECT.id ? PROJECT : { ...PROJECT, id, title: 'Synthetic project B', votes_up: 30, votes_down: 3 };
    const ownership: ProjectOwnership = { project_id: id, project_title: project.title, contributors: [] };
    bodies.set(projectPath(id), project); bodies.set(`${projectPath(id)}/ownership`, ownership);
    bodies.set(`${projectPath(id)}/contributors`, { contributors: [], total: 0 });
  }
  const hold = (id = PROJECT.id) => {
    const pending = deferred<Response>(); plans.set(votePath(id), [...(plans.get(votePath(id)) ?? []), pending.promise]); return pending;
  };
  const holdBody = () => {
    const pending = deferred<unknown>(), response = Response.json(null); response.json = () => pending.promise;
    plans.set(votePath(), [...(plans.get(votePath()) ?? []), Promise.resolve(response)]); return pending;
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL), path = url.pathname + url.search;
    const method = init?.method ?? 'GET', headers = new Headers(init?.headers);
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null, authorization: headers.get('Authorization'), contentType: headers.get('Content-Type') });
    const planned = method === 'POST' ? plans.get(path)?.shift() : undefined;
    if (method === 'GET' ? !bodies.has(path) : !planned) { unexpected.push(`${method} ${path}`); throw new Error('Unexpected synthetic vote request'); }
    return planned ?? Response.json(bodies.get(path));
  }));
  return { calls, unexpected, hold, holdBody };
}
const settle = async () => { await act(async () => {}); };
async function finish<T>(pending: ReturnType<typeof deferred<T>>, value: T) { await act(async () => pending.resolve(value)); }
function openProject(locale: Locale = 'en') {
  const tree = () => <LocaleProvider initialLocale={locale}><ProjectPage /></LocaleProvider>;
  const view = render(tree());
  return { ...view, navigate: async () => { route.id = 'synthetic-b'; view.rerender(tree()); await settle(); } };
}
function voteSection(locale: Locale = 'en') {
  const element = within(screen.getByRole('main')).getByText(publicMessages[locale].votes, { exact: true }).parentElement;
  if (!(element instanceof HTMLElement)) throw new Error('Missing detail vote controls');
  return within(element);
}
function button(direction: Direction = 1, locale: Locale = 'en') {
  return voteSection(locale).getByRole('button', { name: direction === 1 ? /↑|^Upvote|^Поддержать/ : /↓|^Downvote|^Проголосовать против/ });
}
function counts(up: number, down: number, locale: Locale = 'en') {
  expect(button(1, locale).textContent?.replace('↑', '').trim()).toBe(new Intl.NumberFormat(locale).format(up));
  expect(button(-1, locale).textContent?.replace('↓', '').trim()).toBe(new Intl.NumberFormat(locale).format(down));
}
const posts = (http: ReturnType<typeof voteHTTP>) => http.calls.filter(call => call.method === 'POST');
const gets = (http: ReturnType<typeof voteHTTP>) => http.calls.filter(call => call.method === 'GET');
function section(key: 'overview' | 'ownership') { fireEvent.click(screen.getByRole('button', { name: publicMessages.en[key] })); }
beforeEach(() => { route.id = PROJECT.id; localStorage.clear(); vi.useFakeTimers(); vi.setSystemTime(new Date(NOW)); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each([1, -1] as const)('PV1 direction%s sends exact anonymous POST and accepts counts without optimism or GET', async direction => {
  const http = voteHTTP(); openProject(); await settle(); const pending = http.hold();
  const initialGETs = gets(http).length; counts(12, 2); fireEvent.click(button(direction)); counts(12, 2);
  expect(button().hasAttribute('disabled')).toBe(true); expect(button(-1).hasAttribute('disabled')).toBe(true);
  expect(posts(http)).toEqual([{ path: votePath(), method: 'POST', body: { vote: direction }, authorization: null, contentType: 'application/json' }]);
  await finish(pending, Response.json(canonical())); counts(19, 4);
  expect(gets(http)).toHaveLength(initialGETs); expect(posts(http)).toHaveLength(1); expect(http.unexpected).toEqual([]);
});

it('PV1 synchronous repeated and opposite input acquires exactly one operation', async () => {
  const http = voteHTTP(); openProject(); await settle(); const pending = http.hold(), up = button(), down = button(-1);
  act(() => { up.click(); up.click(); down.click(); }); expect(posts(http)).toHaveLength(1);
  await finish(pending, Response.json(canonical())); counts(19, 4); expect(http.unexpected).toEqual([]);
});

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }, { locale: 'ru', mode: 'rate' }] as const)(
  'PV3 $locale $mode failure retains counts, never replays, and accepts explicit opposite direction', async ({ locale, mode }) => {
    const http = voteHTTP(); openProject(locale); await settle(); const pending = http.hold(); fireEvent.click(button(1, locale));
    await act(async () => mode === 'network' ? pending.reject(new TypeError(INTERNAL)) : pending.resolve(Response.json({ detail: INTERNAL }, { status: mode === 'rate' ? 429 : 503 })));
    counts(12, 2, locale); expect(voteSection(locale).getByRole('alert').textContent).toContain(COPY[locale][mode === 'rate' ? 'wait' : 'error']);
    expect(screen.queryByText(INTERNAL)).toBeNull(); expect(voteSection(locale).getAllByRole('button')).toHaveLength(2);
    await act(async () => vi.advanceTimersByTimeAsync(60000)); expect(posts(http)).toHaveLength(1);
    const retry = http.hold(); fireEvent.click(button(-1, locale)); await finish(retry, Response.json(canonical(10, 3)));
    counts(10, 3, locale); expect(posts(http).map(call => call.body)).toEqual([{ vote: 1 }, { vote: -1 }]);
    expect(voteSection(locale).queryByRole('alert')).toBeNull(); expect(http.unexpected).toEqual([]);
  });

it.each(['identity', 'negative', 'fraction', 'unsafe', 'string', 'missing', 'json'] as const)('PV2 malformed %s response cannot replace known counts', async mode => {
  const http = voteHTTP(); openProject(); await settle(); const pending = http.hold(); fireEvent.click(button());
  const invalid = mode === 'identity' ? canonical(19, 4, 'synthetic-b') : mode === 'negative' ? canonical(-1, 4)
    : mode === 'fraction' ? canonical(1.5, 4) : mode === 'unsafe' ? canonical(Number.MAX_SAFE_INTEGER + 1, 4)
      : mode === 'string' ? { ...canonical(), votes_down: '4' } : { project_id: PROJECT.id, votes_up: 19 };
  await finish(pending, mode === 'json' ? new Response('{', { status: 200 }) : Response.json(invalid)); counts(12, 2);
  expect(voteSection().getByRole('alert').textContent).toContain(COPY.en.error); expect(button().hasAttribute('disabled')).toBe(false); expect(http.unexpected).toEqual([]);
});

it('PV4 accepted canonical counts survive a local section roundtrip without another GET', async () => {
  const http = voteHTTP(); openProject(); await settle(); const pending = http.hold(); fireEvent.click(button());
  await finish(pending, Response.json(canonical())); const before = gets(http).length;
  section('ownership'); section('overview'); counts(19, 4); expect(gets(http)).toHaveLength(before); expect(http.unexpected).toEqual([]);
});

it('PV4 a pending project vote completes while overview is hidden and remains canonical on return', async () => {
  const http = voteHTTP(); openProject(); await settle(); const pending = http.hold(); fireEvent.click(button()); section('ownership');
  await finish(pending, Response.json(canonical())); section('overview'); counts(19, 4); expect(posts(http)).toHaveLength(1); expect(http.unexpected).toEqual([]);
});

it('PV4 leaving and returning while pending cannot unlock a second vote', async () => {
  const http = voteHTTP(); openProject(); await settle(); const pending = http.hold(); fireEvent.click(button());
  section('ownership'); section('overview'); expect(button().hasAttribute('disabled')).toBe(true);
  fireEvent.click(button(-1)); expect(posts(http)).toHaveLength(1); await finish(pending, Response.json(canonical())); counts(19, 4); expect(http.unexpected).toEqual([]);
});

it.each(['response', 'body', 'error'] as const)('PV4 old %s cannot overwrite replacement route or unlock its pending operation', async mode => {
  const http = voteHTTP(), view = openProject(); await settle();
  const oldBody = mode === 'body' ? http.holdBody() : null, old = mode !== 'body' ? http.hold() : null;
  fireEvent.click(button()); await settle(); await view.navigate(); const current = http.hold('synthetic-b'); fireEvent.click(button(-1));
  if (oldBody) await finish(oldBody, canonical());
  if (old) await act(async () => mode === 'error' ? old.reject(new TypeError(INTERNAL)) : old.resolve(Response.json(canonical())));
  counts(30, 3); expect(button().hasAttribute('disabled')).toBe(true); expect(voteSection().queryByRole('alert')).toBeNull();
  fireEvent.click(button()); expect(posts(http)).toHaveLength(2);
  await finish(current, Response.json(canonical(40, 7, 'synthetic-b'))); counts(40, 7); expect(http.unexpected).toEqual([]);
});

it.each(['en', 'ru'] as const)('PV5 %s vote actions have meaningful names without invented own-vote selection', async locale => {
  const http = voteHTTP(); openProject(locale); await settle();
  for (const key of ['upvoteProject', 'downvoteProject'] as const) {
    const control = voteSection(locale).getByRole('button', { name: publicMessages[locale][key].replace('{title}', PROJECT.title) });
    expect(control.hasAttribute('aria-pressed')).toBe(false);
  }
  expect(http.unexpected).toEqual([]);
});
