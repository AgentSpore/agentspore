// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import BlogFeedPage from './page';
import BlogPostPage from './[id]/page';
import { API_URL, type BlogPost } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';

const route = vi.hoisted(() => ({ id: '00000000-0000-4000-8000-0000000000a1', surface: 'feed' }));
vi.mock('next/navigation', () => ({ useParams: () => route, usePathname: () => route.surface === 'feed' ? '/blog' : `/blog/${route.id}`, useRouter: () => ({ refresh: vi.fn() }) }));
const A = '00000000-0000-4000-8000-0000000000a1', B = '00000000-0000-4000-8000-0000000000b1';
const BASE = '/api/v1/blog/posts', INTERNAL = 'Synthetic reaction exception';
type Kind = 'like' | 'fire';
type Surface = 'feed' | 'detail';
type Locale = 'en' | 'ru';
const postPath = (id = A) => `${BASE}/${id}`;
const reactionPath = (id = A) => `${postPath(id)}/reactions`;
function post(id = A, like = 1, fire = 0): BlogPost {
  return { id, agent_id: '00000000-0000-4000-8000-0000000000c1', agent_name: 'Synthetic Agent', agent_handle: 'synthetic-agent',
    title: id === A ? 'Synthetic publication A' : 'Synthetic publication B', content: 'Synthetic readable body.', created_at: '2026-10-20T09:00:00Z',
    reactions: { like, fire, insightful: 0, funny: 0 } };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function publicRead(path: string): Response | undefined {
  if (path === `${BASE}?limit=20&offset=0`) return Response.json({ posts: [post()], total: 21, limit: 20, offset: 0 });
  if (path === `${BASE}?limit=20&offset=20`) return Response.json({ posts: [post(B)], total: 21, limit: 20, offset: 20 });
  if ([postPath(A), postPath(B)].includes(path)) return Response.json(post(path === postPath(B) ? B : A));
  if ([`${postPath(A)}/comments`, `${postPath(B)}/comments`].includes(path)) return Response.json([]);
}
function reactionHTTP() {
  const plans = new Map<string, Promise<Response>[]>();
  const calls: { key: string; authorization: string | null; body: unknown }[] = [], unexpected: string[] = [];
  const queue = (method: string, path: string, response: Promise<Response>) => {
    const key = `${method} ${path}`;
    plans.set(key, [...(plans.get(key) ?? []), response]);
  };
  const hold = (method: string, path: string) => { const pending = deferred<Response>(); queue(method, path, pending.promise); return pending; };
  const respond = (method: string, path: string, value: unknown, status = 200) => queue(method, path, Promise.resolve(Response.json(value, { status })));
  const holdBody = (path = postPath()) => {
    const pending = deferred<unknown>(), response = Response.json(null);
    response.json = () => pending.promise;
    queue('GET', path, Promise.resolve(response)); return pending;
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL), method = init?.method ?? 'GET', path = url.pathname + url.search;
    const key = `${method} ${path}`, authorization = new Headers(init?.headers).get('Authorization');
    calls.push({ key, authorization, body: init?.body });
    if (path === '/api/v1/auth/me' && method === 'GET') return Response.json({ id: authorization?.includes('synthetic-B') ? B : A,
      name: 'Synthetic Reader', email: 'reader@example.invalid', avatar_url: null, token_balance: 0, is_admin: false });
    const planned = plans.get(key)?.shift();
    if (planned) return planned;
    if (method === 'GET' && !authorization) {
      const initial = publicRead(path);
      if (initial) return initial;
    }
    unexpected.push(key); throw new Error('Unexpected synthetic reaction request');
  }));
  return { calls, unexpected, hold, respond, holdBody };
}
type HTTP = ReturnType<typeof reactionHTTP>;
const settle = async () => { await act(async () => {}); };
async function finish<T>(pending: ReturnType<typeof deferred<T>>, value: T) { await act(async () => pending.resolve(value)); }
const calls = (http: HTTP, method: string, path: string) => http.calls.filter(call => call.key === `${method} ${path}`);
function article(id = A) {
  const node = screen.getByRole('heading', { name: post(id).title }).closest('article');
  if (!node) throw new Error('Expected publication article');
  return within(node);
}
function reaction(kind: Kind = 'like', id = A, locale: Locale = 'en') {
  const label = publicMessages[locale][kind === 'like' ? 'blogLike' : 'blogFire'], emoji = kind === 'like' ? '👍' : '🔥';
  const button = article(id).getAllByRole('button').find(element => element.getAttribute('aria-label') === label || element.textContent?.includes(emoji));
  if (!button) throw new Error('Expected reaction control');
  return button;
}
const count = (kind: Kind = 'like', id = A, locale: Locale = 'en') => Number(reaction(kind, id, locale).textContent?.match(/\d+/)?.[0] ?? 0);
function retry(locale: Locale = 'en') {
  const message = screen.queryByText(publicMessages[locale].blogReactionCounts) ?? screen.getByText(publicMessages[locale].blogReactionError);
  const notice = message.closest('[role="alert"],[role="status"]');
  if (!(notice instanceof HTMLElement)) throw new Error('Expected accessible reaction notice');
  return within(notice).getByRole('button', { name: sharedMessages[locale].retry });
}
async function open(surface: Surface = 'feed', locale: Locale = 'en') {
  route.surface = surface;
  const scene = () => <LocaleProvider initialLocale={locale}>{surface === 'feed' ? <BlogFeedPage /> : <BlogPostPage />}</LocaleProvider>;
  const view = render(scene()); await settle();
  return { ...view, navigate: async () => { route.id = B; view.rerender(scene()); await settle(); } };
}
async function switchAccount() {
  localStorage.setItem('access_token', 'synthetic-B');
  await act(async () => window.dispatchEvent(new Event('storage')));
}
beforeEach(() => { route.id = A; route.surface = 'feed'; localStorage.clear(); localStorage.setItem('access_token', 'synthetic-A'); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });

it.each([{ surface: 'feed', locale: 'en' }, { surface: 'detail', locale: 'ru' }] as const)(
  'BM.R1 $surface preserves POST201 and POST409→DELETE200 toggle with canonical counts', async ({ surface, locale }) => {
    const http = reactionHTTP(); await open(surface, locale);
    http.respond('POST', reactionPath(), { status: 'added', reaction: 'like' }, 201);
    const canonical = http.hold('GET', postPath());
    fireEvent.click(reaction('like', A, locale)); await settle();
    expect(count('like', A, locale)).toBe(1);
    expect(calls(http, 'POST', reactionPath())).toEqual([{ key: `POST ${reactionPath()}`, authorization: 'Bearer synthetic-A', body: JSON.stringify({ reaction: 'like' }) }]);
    expect(calls(http, 'GET', postPath()).at(-1)?.authorization).toBeNull();
    await finish(canonical, Response.json(post(A, 2))); expect(count('like', A, locale)).toBe(2);
    http.respond('POST', reactionPath(), { detail: 'Reaction already exists' }, 409);
    http.respond('DELETE', `${reactionPath()}/like`, { status: 'removed' });
    http.respond('GET', postPath(), post(A, 1));
    fireEvent.click(reaction('like', A, locale)); await settle();
    expect(calls(http, 'DELETE', `${reactionPath()}/like`)).toEqual([{ key: `DELETE ${reactionPath()}/like`, authorization: 'Bearer synthetic-A', body: undefined }]);
    expect(count('like', A, locale)).toBe(1); expect(screen.queryByText(publicMessages[locale].blogReactionDuplicate)).toBeNull();
    expect(http.unexpected).toEqual([]);
  });
it('BM.R2–3 same pair is singleflight, independent pairs retain newest canonical counts', async () => {
  const http = reactionHTTP(), first = http.hold('POST', reactionPath()), second = http.hold('POST', reactionPath()); await open();
  act(() => { reaction().click(); reaction().click(); });
  expect(calls(http, 'POST', reactionPath())).toHaveLength(1);
  fireEvent.click(reaction('fire')); expect(calls(http, 'POST', reactionPath())).toHaveLength(2);
  const oldBody = http.holdBody(); await finish(first, Response.json({ status: 'added' }, { status: 201 }));
  http.respond('GET', postPath(), post(A, 2, 1)); await finish(second, Response.json({ status: 'added' }, { status: 201 }));
  expect(count()).toBe(2); expect(count('fire')).toBe(1);
  await finish(oldBody, post(A, 1, 0)); expect(count()).toBe(2); expect(count('fire')).toBe(1); expect(http.unexpected).toEqual([]);
  const rejectedBody = http.holdBody(); http.respond('POST', reactionPath(), { status: 'added' }, 201);
  fireEvent.click(reaction()); await settle();
  http.respond('POST', reactionPath(), { status: 'added' }, 201); http.respond('GET', postPath(), post(A, 3, 2));
  fireEvent.click(reaction('fire')); await settle(); expect(count()).toBe(3); expect(count('fire')).toBe(2);
  await act(async () => rejectedBody.reject(new TypeError(INTERNAL)));
  expect(count()).toBe(3); expect(count('fire')).toBe(2);
  expect(screen.queryByText(publicMessages.en.blogReactionCounts)).toBeNull();
  expect(within(screen.getByRole('main')).queryByRole('alert')).toBeNull(); expect(http.unexpected).toEqual([]);
});
it('BM.R4 failed writes retain counts, expose generic outcomes and allow only explicit recovery', async () => {
  const http = reactionHTTP(); await open('detail'); const before = calls(http, 'GET', postPath()).length;
  http.respond('POST', reactionPath(), { detail: INTERNAL }, 503); fireEvent.click(reaction()); await settle();
  expect(screen.getByText(publicMessages.en.blogReactionError)).toBeTruthy(); expect(count()).toBe(1); expect(screen.queryByText(INTERNAL)).toBeNull();
  expect(calls(http, 'GET', postPath())).toHaveLength(before);
  const network = http.hold('POST', reactionPath()); fireEvent.click(retry()); await act(async () => network.reject(new TypeError(INTERNAL)));
  expect(screen.getByText(publicMessages.en.blogReactionError)).toBeTruthy(); expect(count()).toBe(1); expect(calls(http, 'GET', postPath())).toHaveLength(before);
  http.respond('POST', reactionPath(), { detail: 'Reaction already exists' }, 409);
  http.respond('DELETE', `${reactionPath()}/like`, { detail: INTERNAL }, 503); fireEvent.click(retry()); await settle();
  expect(screen.getByText(publicMessages.en.blogReactionError)).toBeTruthy(); expect(count()).toBe(1);
  expect(calls(http, 'POST', reactionPath())).toHaveLength(3); expect(calls(http, 'DELETE', `${reactionPath()}/like`)).toHaveLength(1);
  expect(calls(http, 'GET', postPath())).toHaveLength(before); expect(http.unexpected).toEqual([]);
});
it('BM.R4 anonymous and terminal401 show login without automatic writes or clearing current storage', async () => {
  localStorage.clear(); const http = reactionHTTP(); await open('detail'); fireEvent.click(reaction()); await settle();
  expect(calls(http, 'POST', reactionPath())).toHaveLength(0); expect(screen.getByText(publicMessages.en.blogReactionLogin)).toBeTruthy();
  await switchAccount(); const before = calls(http, 'GET', postPath()).length;
  http.respond('POST', reactionPath(), { detail: INTERNAL }, 401); fireEvent.click(reaction()); await settle();
  expect(screen.getByText(publicMessages.en.blogReactionLogin)).toBeTruthy(); expect(calls(http, 'POST', reactionPath())).toHaveLength(1);
  expect(localStorage.getItem('access_token')).toBe('synthetic-B'); expect(calls(http, 'GET', postPath())).toHaveLength(before);
  expect(screen.getByRole('link', { name: /Sign in/i }).getAttribute('href')).toBe('/login'); expect(http.unexpected).toEqual([]);
});
it('BM.R4 accepted add and removal recover counts through GET-only Retry', async () => {
  const http = reactionHTTP(); await open();
  http.respond('POST', reactionPath(), { status: 'added' }, 201); http.respond('GET', postPath(), { detail: INTERNAL }, 503);
  fireEvent.click(reaction()); await settle(); expect(screen.getByText(publicMessages.en.blogReactionCounts)).toBeTruthy(); expect(count()).toBe(1);
  http.respond('GET', postPath(), post(A, 2)); fireEvent.click(retry()); await settle(); expect(count()).toBe(2);
  expect(calls(http, 'POST', reactionPath())).toHaveLength(1); expect(calls(http, 'GET', postPath())).toHaveLength(2);
  http.respond('POST', reactionPath(), { detail: 'Reaction already exists' }, 409); http.respond('DELETE', `${reactionPath()}/like`, { status: 'removed' });
  const refresh = http.hold('GET', postPath()); fireEvent.click(reaction()); await settle(); await act(async () => refresh.reject(new TypeError(INTERNAL)));
  expect(screen.getByText(publicMessages.en.blogReactionCounts)).toBeTruthy(); expect(count()).toBe(2);
  http.respond('GET', postPath(), post(A, 1)); fireEvent.click(retry()); await settle(); expect(count()).toBe(1);
  expect(calls(http, 'POST', reactionPath())).toHaveLength(2); expect(calls(http, 'DELETE', `${reactionPath()}/like`)).toHaveLength(1);
  expect(calls(http, 'GET', postPath())).toHaveLength(4); expect(http.unexpected).toEqual([]);
});
it('BM.R5 delayed A canonical body cannot replace detail B', async () => {
  const http = reactionHTTP(), view = await open('detail');
  http.respond('POST', reactionPath(), { status: 'added' }, 201); const oldBody = http.holdBody();
  fireEvent.click(reaction()); await settle(); await view.navigate(); await finish(oldBody, post(A, 9));
  expect(count('like', B)).toBe(1); expect(screen.queryByRole('heading', { name: post().title })).toBeNull(); expect(http.unexpected).toEqual([]);
});
it('BM.R5 accepting a new feed page invalidates held old POST before canonical GET', async () => {
  const http = reactionHTTP(), pending = http.hold('POST', reactionPath()); await open(); fireEvent.click(reaction());
  fireEvent.click(screen.getByRole('button', { name: 'Next' })); await settle(); expect(article(B)).toBeTruthy();
  await finish(pending, Response.json({ status: 'added' }, { status: 201 }));
  expect(calls(http, 'GET', postPath())).toHaveLength(0); expect(count('like', B)).toBe(1); expect(http.unexpected).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: 'Prev' })); await settle();
  expect(reaction().hasAttribute('disabled')).toBe(false);
  expect(within(screen.getByRole('main')).queryByRole('status')).toBeNull();
  http.respond('POST', reactionPath(), { status: 'added' }, 201); http.respond('GET', postPath(), post(A, 2));
  fireEvent.click(reaction()); await settle(); expect(count()).toBe(2);
  expect(calls(http, 'POST', reactionPath())).toHaveLength(2); expect(http.unexpected).toEqual([]);
});
it('BM.R5 old account completion cannot publish failure or release a newer pair lock', async () => {
  const http = reactionHTTP(), old = http.hold('POST', reactionPath()); await open('detail'); fireEvent.click(reaction());
  await switchAccount(); const current = http.hold('POST', reactionPath()); fireEvent.click(reaction());
  expect(calls(http, 'POST', reactionPath())).toHaveLength(2);
  await finish(old, Response.json({ detail: INTERNAL }, { status: 503 }));
  expect(screen.queryByText(publicMessages.en.blogReactionError)).toBeNull(); fireEvent.click(reaction());
  expect(calls(http, 'POST', reactionPath())).toHaveLength(2);
  http.respond('GET', postPath(), post(A, 2)); await finish(current, Response.json({ status: 'added' }, { status: 201 }));
  expect(count()).toBe(2); expect(calls(http, 'POST', reactionPath())[1]?.authorization).toBe('Bearer synthetic-B'); expect(http.unexpected).toEqual([]);
});
it('BM.R5 unmounted held POST cannot start a canonical GET', async () => {
  const http = reactionHTTP(), pending = http.hold('POST', reactionPath()), view = await open(); fireEvent.click(reaction()); view.unmount();
  const before = http.calls.length; await finish(pending, Response.json({ status: 'added' }, { status: 201 }));
  expect(http.calls).toHaveLength(before); expect(calls(http, 'GET', postPath())).toHaveLength(0); expect(http.unexpected).toEqual([]);
});
