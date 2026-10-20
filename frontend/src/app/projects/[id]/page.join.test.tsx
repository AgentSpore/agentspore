// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProjectPage from './page';
import { API_URL, type ProjectOwnership } from '@/lib/api';
import { type HumanContributor, type Project } from './useProjectReads';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';

const route = vi.hoisted(() => ({ id: 'synthetic-a' }));
vi.mock('next/navigation', () => ({ useParams: () => route, usePathname: () => `/projects/${route.id}`, useRouter: () => ({ refresh: vi.fn() }) }));
type Locale = 'en' | 'ru';
const NOW = '2026-10-20T12:00:00Z', INTERNAL = 'Synthetic internal join failure';
const COPY = {
  en: { error: 'Could not request to join. Try again.', again: 'Sign in again', already: 'You are already a contributor.' },
  ru: { error: 'Не удалось отправить заявку. Попробуйте ещё раз.', again: 'Войти снова', already: 'Вы уже участвуете в проекте.' },
};
const PROJECT: Project = { id: 'synthetic-a', title: 'Synthetic project A', description: 'Synthetic overview', category: 'tools',
  status: 'active', repo_url: null, deploy_url: null, tech_stack: [], agent_name: 'Synthetic agent', agent_handle: 'synthetic-agent',
  creator_agent_id: 'synthetic-agent', votes_up: 12, votes_down: 2, created_at: NOW };
const path = (id = PROJECT.id) => `/api/v1/projects/${id}/contributors`;
const joinPath = (id = PROJECT.id) => `${path(id)}/join`;
const accepted = (status = 'pending_approval') => Response.json({ status, message: 'Synthetic server-only message' });
function members(userId = 'synthetic-other'): { contributors: HumanContributor[] } {
  return { contributors: [{ id: 'synthetic-membership', role: 'contributor', contribution_points: 3, joined_at: NOW,
    user_id: userId, user_name: 'Synthetic known member', user_email: 'member@example.invalid', wallet_address: null }] };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function joinHTTP() {
  const plans = new Map<string, Promise<Response>[]>(), bodies = new Map<string, unknown>();
  const calls: { key: string; body: unknown; authorization: string | null }[] = [], unexpected: string[] = [];
  for (const id of [PROJECT.id, 'synthetic-b']) {
    const project = { ...PROJECT, id, title: id === PROJECT.id ? PROJECT.title : 'Synthetic project B' };
    const ownership: ProjectOwnership = { project_id: id, project_title: project.title, contributors: [] };
    bodies.set(`/api/v1/projects/${id}`, project); bodies.set(`/api/v1/projects/${id}/ownership`, ownership); bodies.set(path(id), members());
  }
  const hold = (key = `POST ${joinPath()}`) => {
    const pending = deferred<Response>(); plans.set(key, [...(plans.get(key) ?? []), pending.promise]); return pending;
  };
  const holdBody = () => {
    const pending = deferred<unknown>(), response = Response.json(null); response.json = () => pending.promise;
    plans.set(`POST ${joinPath()}`, [...(plans.get(`POST ${joinPath()}`) ?? []), Promise.resolve(response)]); return pending;
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL), requestPath = url.pathname + url.search;
    const method = init?.method ?? 'GET', key = `${method} ${requestPath}`, authorization = new Headers(init?.headers).get('Authorization');
    calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : null, authorization });
    if (method === 'GET' && requestPath === '/api/v1/auth/me') {
      const id = authorization === 'Bearer synthetic-b-token' ? 'synthetic-b-user' : 'synthetic-a-user';
      return Response.json({ id, email: `${id}@example.invalid`, name: 'Synthetic account', avatar_url: null, token_balance: 0, is_admin: false });
    }
    const planned = plans.get(key)?.shift();
    if (planned) return planned;
    if (method === 'GET' && bodies.has(requestPath)) return Response.json(bodies.get(requestPath));
    unexpected.push(key); throw new Error('Unexpected synthetic join request');
  }));
  return { calls, unexpected, bodies, hold, holdBody };
}
const main = () => within(screen.getByRole('main'));
const settle = async () => { await act(async () => {}); };
async function finish<T>(pending: ReturnType<typeof deferred<T>>, value: T) { await act(async () => pending.resolve(value)); }
function auth(owner = 'a') {
  localStorage.setItem('auth', JSON.stringify({ token: `synthetic-${owner}-token`, userId: `synthetic-${owner}-user`, email: `${owner}@example.invalid` }));
  localStorage.setItem('access_token', `synthetic-${owner}-token`); window.dispatchEvent(new Event('storage'));
}
function select(locale: Locale = 'en') { fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${publicMessages[locale].contributors}`) })); }
function openProject(locale: Locale = 'en') {
  const tree = () => <LocaleProvider initialLocale={locale}><ProjectPage /></LocaleProvider>;
  const view = render(tree());
  return { ...view, navigate: async () => { route.id = 'synthetic-b'; view.rerender(tree()); await settle(); select(locale); } };
}
const join = (locale: Locale = 'en') => main().getByRole('button', { name: publicMessages[locale].requestToJoin });
const posts = (http: ReturnType<typeof joinHTTP>) => http.calls.filter(call => call.key.startsWith('POST '));
const reads = (http: ReturnType<typeof joinHTTP>, id = PROJECT.id) => http.calls.filter(call => call.key === `GET ${path(id)}`);
const knownMember = () => expect(main().getByText('Synthetic known member')).toBeTruthy();
beforeEach(() => { route.id = PROJECT.id; localStorage.clear(); vi.useFakeTimers(); vi.setSystemTime(new Date(NOW)); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('PJ1 anonymous join opens the existing local modal without POST', async () => {
  const http = joinHTTP(); openProject(); await settle(); select(); fireEvent.click(join());
  expect(screen.getByRole('heading', { name: publicMessages.en.signInToContinue })).toBeTruthy();
  expect(posts(http)).toEqual([]); expect(http.unexpected).toEqual([]);
});
it('PJ1 confirmed existing membership hides the join action', async () => {
  const http = joinHTTP(); http.bodies.set(path(), members('synthetic-a-user')); auth(); openProject(); await settle(); select();
  knownMember(); expect(main().queryByRole('button', { name: publicMessages.en.requestToJoin })).toBeNull(); expect(posts(http)).toEqual([]);
});
it.each([{ locale: 'en', status: 'auto_approved', key: 'youAreNowAContributor' },
  { locale: 'ru', status: 'pending_approval', key: 'yourRequestIsPendingApproval' }] as const)(
  'PJ2 $locale accepted $status sends exact Bearer payload and refreshes contributors once', async ({ locale, status, key }) => {
    const http = joinHTTP(); auth(); openProject(locale); await settle(); select(locale); const pending = http.hold();
    fireEvent.click(join(locale)); expect(reads(http)).toHaveLength(1);
    expect(posts(http)).toEqual([{ key: `POST ${joinPath()}`, body: { message: publicMessages.en.iDLikeToContributeToThisProject }, authorization: 'Bearer synthetic-a-token' }]);
    await finish(pending, accepted(status)); expect(main().getByText(publicMessages[locale][key])).toBeTruthy();
    expect(reads(http)).toHaveLength(2); expect(main().queryByRole('button', { name: publicMessages[locale].requestToJoin })).toBeNull();
    expect(http.unexpected).toEqual([]);
  });
it('PJ1 synchronous repeated input acquires one join operation', async () => {
  const http = joinHTTP(); auth(); openProject(); await settle(); select(); const pending = http.hold(), button = join();
  act(() => { button.click(); button.click(); button.click(); }); expect(posts(http)).toHaveLength(1);
  await finish(pending, accepted()); expect(reads(http)).toHaveLength(2); expect(http.unexpected).toEqual([]);
});
it('PJ2 conflict means already contributor and refreshes once without replay', async () => {
  const http = joinHTTP(); auth(); openProject(); await settle(); select(); const pending = http.hold(); fireEvent.click(join());
  http.bodies.set(path(), members('synthetic-a-user')); await finish(pending, Response.json({ detail: INTERNAL }, { status: 409 }));
  expect(main().getByRole('status').textContent).toContain(COPY.en.already); expect(main().queryByText(publicMessages.en.yourRequestIsPendingApproval)).toBeNull();
  expect(reads(http)).toHaveLength(2); expect(posts(http)).toHaveLength(1); knownMember(); expect(screen.queryByText(INTERNAL)).toBeNull();
});
it('PJ3 Russian401 exposes sign-in-again entry without clearing auth or replaying POST', async () => {
  const http = joinHTTP(); auth(); openProject('ru'); await settle(); select('ru'); const pending = http.hold(); fireEvent.click(join('ru'));
  await finish(pending, Response.json({ detail: INTERNAL }, { status: 401 }));
  expect(main().getByRole('alert').textContent).toContain(COPY.ru.again); const button = main().getByRole('button', { name: COPY.ru.again });
  fireEvent.click(button); expect(screen.getByRole('heading', { name: publicMessages.ru.signInToContinue })).toBeTruthy();
  expect(localStorage.getItem('access_token')).toBe('synthetic-a-token'); expect(posts(http)).toHaveLength(1); expect(reads(http)).toHaveLength(1);
});
it.each([{ locale: 'en', mode: '404' }, { locale: 'ru', mode: '503' }, { locale: 'en', mode: 'network' },
  { locale: 'ru', mode: 'json' }, { locale: 'en', mode: 'status' }, { locale: 'en', mode: '201' }] as const)(
  'PJ3 $locale $mode failure retains rows and only explicit join retries POST', async ({ locale, mode }) => {
    const http = joinHTTP(); auth(); openProject(locale); await settle(); select(locale); const pending = http.hold(); fireEvent.click(join(locale));
    await act(async () => {
      if (mode === 'network') pending.reject(new TypeError(INTERNAL));
      else if (mode === 'json') pending.resolve(new Response('{', { status: 200 }));
      else if (mode === 'status') pending.resolve(Response.json({ status: 'unrecognized', detail: INTERNAL }));
      else pending.resolve(Response.json({ status: 'auto_approved', detail: INTERNAL }, { status: Number(mode) }));
    });
    expect(main().getByRole('alert').textContent).toContain(COPY[locale].error); knownMember();
    expect(screen.queryByText(INTERNAL)).toBeNull(); expect(main().queryByText(publicMessages[locale].yourRequestIsPendingApproval)).toBeNull();
    expect(reads(http)).toHaveLength(1); expect(posts(http)).toHaveLength(1);
    const next = http.hold(); fireEvent.click(join(locale)); await finish(next, accepted());
    expect(posts(http)).toHaveLength(2); expect(reads(http)).toHaveLength(2); expect(http.unexpected).toEqual([]);
  });
it('PJ2 accepted join survives refresh failure and section Retry only repeats GET', async () => {
  const http = joinHTTP(); auth(); openProject(); await settle(); select(); const pending = http.hold(), refresh = http.hold(`GET ${path()}`);
  fireEvent.click(join()); await finish(pending, accepted('auto_approved')); await finish(refresh, Response.json({ detail: INTERNAL }, { status: 503 }));
  expect(main().getByText(publicMessages.en.youAreNowAContributor)).toBeTruthy(); knownMember();
  fireEvent.click(main().getByRole('button', { name: sharedMessages.en.retry })); await settle();
  expect(reads(http)).toHaveLength(3); expect(posts(http)).toHaveLength(1); expect(main().getByText(publicMessages.en.youAreNowAContributor)).toBeTruthy();
});
it('PJ4 switching account clears an accepted notice without replaying join', async () => {
  const http = joinHTTP(); auth(); openProject(); await settle(); select(); const pending = http.hold(); fireEvent.click(join()); await finish(pending, accepted());
  await act(async () => auth('b')); expect(main().queryByText(publicMessages.en.yourRequestIsPendingApproval)).toBeNull();
  expect(join()).toBeTruthy(); expect(posts(http)).toHaveLength(1); expect(reads(http).at(-1)?.authorization).toBe('Bearer synthetic-b-token');
});
it.each(['response', 'body', 'error'] as const)('PJ4 old account %s cannot publish, refresh, or unlock the newer join', async mode => {
  const http = joinHTTP(); auth(); openProject(); await settle(); select();
  const oldBody = mode === 'body' ? http.holdBody() : null, old = mode !== 'body' ? http.hold() : null; fireEvent.click(join()); await settle();
  await act(async () => auth('b')); const before = reads(http).length, current = http.hold(); fireEvent.click(join());
  if (oldBody) await finish(oldBody, { status: 'auto_approved' });
  if (old) await act(async () => mode === 'error' ? old.reject(new TypeError(INTERNAL)) : old.resolve(accepted('auto_approved')));
  expect(main().queryByText(publicMessages.en.youAreNowAContributor)).toBeNull(); expect(main().queryByRole('alert')).toBeNull(); expect(reads(http)).toHaveLength(before);
  const pending = main().getByRole('button', { name: publicMessages.en.requesting }); expect(pending.hasAttribute('disabled')).toBe(true);
  fireEvent.click(pending); expect(posts(http)).toHaveLength(2); await finish(current, accepted());
  expect(main().getByText(publicMessages.en.yourRequestIsPendingApproval)).toBeTruthy(); expect(reads(http)).toHaveLength(before + 1); expect(http.unexpected).toEqual([]);
});
it('PJ4 canonical generation-only change invalidates the previous join while legacy identity stays equal', async () => {
  const http = joinHTTP(); auth(); openProject(); await settle(); select(); const old = http.hold(); fireEvent.click(join());
  await act(async () => { localStorage.setItem('refresh_token', 'synthetic-refresh-replacement'); window.dispatchEvent(new Event('storage')); });
  const current = http.hold(), before = reads(http).length; fireEvent.click(join()); await finish(old, accepted('auto_approved'));
  expect(main().queryByText(publicMessages.en.youAreNowAContributor)).toBeNull(); expect(reads(http)).toHaveLength(before);
  expect(main().getByRole('button', { name: publicMessages.en.requesting }).hasAttribute('disabled')).toBe(true);
  await finish(current, accepted()); expect(posts(http)).toHaveLength(2); expect(reads(http)).toHaveLength(before + 1);
});
it('PJ4 deferred join body from the previous route cannot affect the replacement project', async () => {
  const http = joinHTTP(); auth(); const view = openProject(); await settle(); select(); const old = http.holdBody(); fireEvent.click(join()); await settle();
  await view.navigate(); const current = http.hold(`POST ${joinPath('synthetic-b')}`); fireEvent.click(join());
  await finish(old, { status: 'auto_approved' }); expect(reads(http, 'synthetic-b')).toHaveLength(1);
  expect(main().queryByText(publicMessages.en.youAreNowAContributor)).toBeNull();
  expect(main().getByRole('button', { name: publicMessages.en.requesting }).hasAttribute('disabled')).toBe(true);
  await finish(current, accepted()); expect(reads(http, 'synthetic-b')).toHaveLength(2); expect(http.unexpected).toEqual([]);
});
