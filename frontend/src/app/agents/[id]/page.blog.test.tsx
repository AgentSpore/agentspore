// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import AgentPage from './page';
import { API_URL, REACTION_META, type Agent, type BlogPost, type BlogPostsResponse } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';

const route = vi.hoisted(() => ({ id: 'synthetic-a' }));
vi.mock('next/navigation', () => ({ useParams: () => route, useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));
type Locale = 'en' | 'ru';
type Reaction = 'like' | 'fire';
const NOW = '2026-10-20T12:00:00Z';
const INTERNAL = 'Synthetic internal blog failure';
const SYNTHETIC_ACCESS = 'synthetic-blog-access';
const COPY = {
  en: { login: 'Sign in to react.', duplicate: 'You already added this reaction.', write: 'Could not save reaction.', counts: 'Reaction saved. Could not refresh counts.' },
  ru: { login: 'Войдите, чтобы отреагировать.', duplicate: 'Вы уже добавили эту реакцию.', write: 'Не удалось сохранить реакцию.', counts: 'Реакция сохранена. Не удалось обновить счётчики.' },
};
const A: Agent = { id: 'synthetic-a', name: 'Synthetic Agent A', handle: 'synthetic-a', agent_type: 'external',
  model_provider: 'synthetic', model_name: 'synthetic', specialization: 'programmer', skills: [], karma: 0,
  projects_created: 0, code_commits: 0, reviews_done: 0, last_heartbeat: null, is_active: true, created_at: NOW,
  dna_risk: 5, dna_speed: 5, dna_verbosity: 5, dna_creativity: 5, bio: null, fork_count: 0, is_hosted: false };
function post(index = 0, agentId = A.id): BlogPost {
  return { id: `${agentId}-post-${index}`, agent_id: agentId, agent_name: agentId, agent_handle: agentId,
    title: `Title ${agentId} ${index}`, content: `Body ${agentId} ${index}`, created_at: NOW,
    reactions: { like: 0, fire: 0, insightful: 0, funny: 0 } };
}
function page(offset = 0, total = 21, id = A.id): BlogPostsResponse {
  return { posts: Array.from({ length: Math.max(0, Math.min(10, total - offset)) }, (_, index) => post(offset + index, id)), total, limit: 10, offset };
}
const collection = (offset = 0, id = A.id) => `/api/v1/blog/agents/${id}/posts?limit=10&offset=${offset}`;
const single = (index = 0) => `/api/v1/blog/posts/${post(index).id}`;
const reactionPath = (index = 0) => `${single(index)}/reactions`;
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function blogHTTP() {
  const plans = new Map<string, Promise<Response>[]>(), bodies = new Map<string, unknown>();
  const calls: { path: string; method: string; body: unknown; authorization: string | null }[] = [], unexpected: string[] = [];
  for (const id of [A.id, 'synthetic-b']) {
    bodies.set(`/api/v1/agents/${id}`, { ...A, id, name: `Agent ${id}` });
    bodies.set(`/api/v1/activity?agent_id=${id}&limit=50`, []);
    bodies.set(`/api/v1/agents/${id}/model-usage`, { total_calls: 0, unique_models: 0, by_model: [], by_task: [] });
    bodies.set(`/api/v1/agents/${id}/github-activity?limit=50`, { activities: [] });
    bodies.set(`/api/v1/agents/${id}/badges`, []);
    for (const offset of [0, 10, 20]) bodies.set(collection(offset, id), page(offset, 21, id));
  }
  for (let index = 0; index < 21; index++) bodies.set(single(index), post(index));
  const enqueue = (method: string, path: string, response: Promise<Response>) => plans.set(`${method} ${path}`, [...(plans.get(`${method} ${path}`) ?? []), response]);
  const hold = (method: string, path: string) => { const pending = deferred<Response>(); enqueue(method, path, pending.promise); return pending; };
  const holdBody = (path: string) => {
    const body = deferred<unknown>(), response = Response.json(null);
    response.json = () => body.promise; enqueue('GET', path, Promise.resolve(response)); return body;
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL), path = url.pathname + url.search;
    const method = init?.method ?? 'GET', authorization = new Headers(init?.headers).get('Authorization');
    const planned = plans.get(`${method} ${path}`)?.shift();
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null, authorization });
    if (method === 'GET' ? !bodies.has(path) || Boolean(authorization) : !planned) {
      unexpected.push(`${method} ${path}`); throw new Error('Unexpected synthetic request');
    }
    return planned ?? Response.json(bodies.get(path));
  }));
  return { calls, unexpected, hold, holdBody, bodies };
}
function openBlog(locale: Locale = 'en') {
  const tree = () => <LocaleProvider initialLocale={locale}><AgentPage /></LocaleProvider>;
  const view = render(tree());
  return { ...view, navigate: async (id: string) => { route.id = id; view.rerender(tree()); await settle(); } };
}
const settle = async () => { await act(async () => {}); };
async function finish<T>(pending: ReturnType<typeof deferred<T>>, value: T) { await act(async () => pending.resolve(value)); }
function card(index = 0) {
  const element = screen.getByRole('heading', { name: post(index).title }).closest('.blog-card');
  if (!(element instanceof HTMLElement)) throw new Error('Missing blog card');
  return within(element);
}
const reactionButton = (index = 0, reaction: Reaction = 'like') => card(index).getByRole('button', { name: new RegExp(`${REACTION_META[reaction].emoji}|${reaction === 'like' ? 'Like|Нравится' : 'Fire|Огонь'}`) });
const next = (locale: Locale = 'en') => screen.getByRole('button', { name: new RegExp(publicMessages[locale].next) });
const prev = () => screen.getByRole('button', { name: new RegExp(publicMessages.en.prev) });
const retry = (locale: Locale = 'en') => screen.getByRole('button', { name: sharedMessages[locale].retry });
const reads = (http: ReturnType<typeof blogHTTP>, path: string) => http.calls.filter(call => call.path === path && call.method === 'GET');
const writes = (http: ReturnType<typeof blogHTTP>) => http.calls.filter(call => call.method === 'POST');
beforeEach(() => { route.id = A.id; localStorage.clear(); localStorage.setItem('access_token', SYNTHETIC_ACCESS); vi.useFakeTimers(); vi.setSystemTime(new Date(NOW)); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }] as const)(
  'AB.1 $locale initial $mode is unavailable, not empty, and Retry recovers', async ({ locale, mode }) => {
    const http = blogHTTP(), pending = http.hold('GET', collection()); openBlog(locale); await settle();
    expect(screen.getByText(publicMessages[locale].loadingPosts)).toBeTruthy();
    expect(screen.queryByText(publicMessages[locale].noBlogPostsYet)).toBeNull();
    await act(async () => mode === 'http' ? pending.resolve(Response.json({ detail: INTERNAL }, { status: 503 })) : pending.reject(new TypeError(INTERNAL)));
    expect(screen.getByRole('alert').textContent).toContain(sharedMessages[locale].couldNotLoadData);
    expect(screen.queryByText(publicMessages[locale].noBlogPostsYet)).toBeNull(); expect(screen.queryByText(INTERNAL)).toBeNull();
    fireEvent.click(retry(locale)); await settle();
    expect(screen.getByText(post().content)).toBeTruthy(); expect(reads(http, collection())).toHaveLength(2); expect(http.unexpected).toEqual([]);
  });

it('AB.1 successful empty is distinct from failure', async () => {
  const http = blogHTTP(); http.bodies.set(collection(), page(0, 0)); openBlog(); await settle();
  expect(screen.getByText(publicMessages.en.noBlogPostsYet)).toBeTruthy(); expect(screen.queryByRole('alert')).toBeNull(); expect(http.unexpected).toEqual([]);
});

it('AB.2 paging failure retains accepted rows and Retry repeats failed offset before final bounds', async () => {
  const http = blogHTTP(); openBlog(); await settle(); const failed = http.hold('GET', collection(10));
  fireEvent.click(next()); await finish(failed, Response.json({ detail: INTERNAL }, { status: 503 }));
  expect(screen.getByText(post().content)).toBeTruthy(); expect(screen.getByRole('alert')).toBeTruthy();
  expect(prev().hasAttribute('disabled')).toBe(true); fireEvent.click(retry()); await settle();
  expect(screen.getByText(post(10).content)).toBeTruthy(); expect(reads(http, collection(10))).toHaveLength(2);
  fireEvent.click(next()); await settle(); expect(screen.getByText(post(20).content)).toBeTruthy(); expect(next().hasAttribute('disabled')).toBe(true);
  fireEvent.click(prev()); await settle(); expect(screen.getByText(post(10).content)).toBeTruthy(); expect(http.unexpected).toEqual([]);
});

it('AB.2 synchronous paging input has one read and no hidden page queue', async () => {
  const http = blogHTTP(); openBlog(); await settle(); const pending = http.hold('GET', collection(10)), button = next();
  act(() => { button.click(); button.click(); });
  expect(reads(http, collection(10))).toHaveLength(1); expect(next().hasAttribute('disabled')).toBe(true); expect(prev().hasAttribute('disabled')).toBe(true);
  await finish(pending, Response.json(page(10))); expect(screen.getByText(post(10).content)).toBeTruthy(); expect(reads(http, collection(20))).toHaveLength(0); expect(http.unexpected).toEqual([]);
});

it.each(['body', 'error', 'unmount'] as const)('AB.3 old blog %s cannot replace new route or create follow-up reads', async mode => {
  const http = blogHTTP(), oldBody = mode === 'body' ? http.holdBody(collection()) : null;
  const old = mode !== 'body' ? http.hold('GET', collection()) : null, view = openBlog(); await settle();
  if (mode === 'unmount') view.unmount(); else await view.navigate('synthetic-b');
  const count = http.calls.length;
  if (oldBody) await finish(oldBody, page());
  if (old) await act(async () => mode === 'error' ? old.reject(new TypeError(INTERNAL)) : old.resolve(Response.json(page())));
  if (mode !== 'unmount') { expect(screen.getByText(post(0, 'synthetic-b').content)).toBeTruthy(); expect(screen.queryByText(post().content)).toBeNull(); expect(screen.queryByRole('alert')).toBeNull(); }
  expect(http.calls).toHaveLength(count); expect(http.unexpected).toEqual([]);
});

it('AB.4 accepted reaction sends exact user payload and uses canonical counts without optimism', async () => {
  const http = blogHTTP(); openBlog(); await settle(); const pending = http.hold('POST', reactionPath()), canonical = http.hold('GET', single());
  fireEvent.click(reactionButton()); expect(reactionButton().textContent).not.toContain('7');
  expect(writes(http)).toEqual([{ path: reactionPath(), method: 'POST', body: { reaction: 'like' }, authorization: `Bearer ${SYNTHETIC_ACCESS}` }]);
  expect(reads(http, single())).toHaveLength(0); await finish(pending, Response.json({ status: 'added', reaction: 'like' }, { status: 201 }));
  expect(reads(http, single())).toHaveLength(1); await finish(canonical, Response.json({ ...post(), reactions: { ...post().reactions, like: 7 } }));
  expect(reactionButton().textContent).toContain('7'); expect(writes(http)).toHaveLength(1); expect(http.unexpected).toEqual([]);
});

it.each([{ locale: 'en', status: 401, copy: 'login' }, { locale: 'ru', status: 409, copy: 'duplicate' }] as const)(
  'AB.4 $status in $locale is explicit without canonical success', async ({ locale, status, copy }) => {
    const http = blogHTTP(); openBlog(locale); await settle(); const pending = http.hold('POST', reactionPath());
    fireEvent.click(reactionButton()); await finish(pending, Response.json({ detail: INTERNAL }, { status }));
    expect(screen.getByText(COPY[locale][copy])).toBeTruthy(); expect(reads(http, single())).toHaveLength(0); expect(screen.queryByText(INTERNAL)).toBeNull();
    if (status === 401) expect(screen.getByRole('link', { name: /sign in|log in/i }).getAttribute('href')).toBe('/login');
    expect(writes(http)).toHaveLength(1); expect(http.unexpected).toEqual([]);
  });

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }] as const)(
  'AB.4 $mode in $locale offers explicit POST Retry of the same pair', async ({ locale, mode }) => {
    const http = blogHTTP(); openBlog(locale); await settle(); const pending = http.hold('POST', reactionPath());
    fireEvent.click(reactionButton()); await act(async () => mode === 'http' ? pending.resolve(Response.json({ detail: INTERNAL }, { status: 503 })) : pending.reject(new TypeError(INTERNAL)));
    expect(screen.getByText(COPY[locale].write)).toBeTruthy(); expect(reads(http, single())).toHaveLength(0); expect(screen.queryByText(INTERNAL)).toBeNull();
    const accepted = http.hold('POST', reactionPath()); fireEvent.click(retry(locale));
    await finish(accepted, Response.json({ status: 'added', reaction: 'like' }, { status: 201 }));
    expect(writes(http).map(call => call.body)).toEqual([{ reaction: 'like' }, { reaction: 'like' }]); expect(http.unexpected).toEqual([]);
  });

it('AB.4 duplicate input locks exactly one pair while independent pairs remain usable', async () => {
  const http = blogHTTP(); openBlog(); await settle(); const first = http.hold('POST', reactionPath()), second = http.hold('POST', reactionPath(1));
  const button = reactionButton(); act(() => { button.click(); button.click(); });
  expect(writes(http)).toHaveLength(1); expect(reactionButton().hasAttribute('disabled')).toBe(true);
  fireEvent.click(reactionButton(1)); expect(writes(http)).toHaveLength(2);
  await finish(second, Response.json({ status: 'added', reaction: 'like' }, { status: 201 }));
  expect(reactionButton().hasAttribute('disabled')).toBe(true); await finish(first, Response.json({ status: 'added', reaction: 'like' }, { status: 201 })); expect(http.unexpected).toEqual([]);
});

it.each(['en', 'ru'] as const)('AB.5 %s accepted write with failed counts retries only canonical GET', async locale => {
  const http = blogHTTP(); openBlog(locale); await settle(); const accepted = http.hold('POST', reactionPath()), canonical = http.hold('GET', single());
  fireEvent.click(reactionButton()); await finish(accepted, Response.json({ status: 'added', reaction: 'like' }, { status: 201 }));
  await finish(canonical, Response.json({ detail: INTERNAL }, { status: 503 }));
  expect(screen.getByText(COPY[locale].counts)).toBeTruthy(); expect(reactionButton().textContent).not.toContain('9');
  http.bodies.set(single(), { ...post(), reactions: { ...post().reactions, like: 9 } }); fireEvent.click(retry(locale)); await settle();
  expect(reactionButton().textContent).toContain('9'); expect(writes(http)).toHaveLength(1); expect(reads(http, single())).toHaveLength(2); expect(http.unexpected).toEqual([]);
});

it.each(['route', 'page', 'unmount'] as const)('AB.3 late accepted reaction on old %s cannot mutate current context or trigger canonical read', async mode => {
  const http = blogHTTP(), view = openBlog(); await settle(); const old = http.hold('POST', reactionPath());
  fireEvent.click(reactionButton());
  if (mode === 'route') await view.navigate('synthetic-b');
  else if (mode === 'page') { fireEvent.click(next()); await settle(); }
  else view.unmount();
  const before = http.calls.length; await finish(old, Response.json({ status: 'added', reaction: 'like' }, { status: 201 }));
  expect(http.calls).toHaveLength(before);
  if (mode === 'page') expect(screen.getByText(post(10).content)).toBeTruthy();
  if (mode === 'route') expect(screen.getByText(post(0, 'synthetic-b').content)).toBeTruthy();
  expect(http.unexpected).toEqual([]);
});


it('AB.3 deferred canonical body cannot overwrite a newly accepted blog page', async () => {
  const http = blogHTTP(); openBlog(); await settle();
  const accepted = http.hold('POST', reactionPath()), body = http.holdBody(single());
  fireEvent.click(reactionButton()); await finish(accepted, Response.json({ status: 'added', reaction: 'like' }, { status: 201 }));
  fireEvent.click(next()); await settle();
  await finish(body, { ...post(), reactions: { ...post().reactions, like: 99 } });
  expect(screen.getByText(post(10).content)).toBeTruthy(); expect(screen.queryByText('99')).toBeNull(); expect(http.unexpected).toEqual([]);
});

it('AB.4 independent reaction types on one post keep their own pending ownership', async () => {
  const http = blogHTTP(); openBlog(); await settle();
  const like = http.hold('POST', reactionPath()), fire = http.hold('POST', reactionPath());
  fireEvent.click(reactionButton()); fireEvent.click(reactionButton(0, 'fire'));
  expect(writes(http).map(call => call.body)).toEqual([{ reaction: 'like' }, { reaction: 'fire' }]);
  await finish(fire, Response.json({ status: 'added', reaction: 'fire' }, { status: 201 }));
  expect(reactionButton().hasAttribute('disabled')).toBe(true); expect(reactionButton(0, 'fire').hasAttribute('disabled')).toBe(false);
  await finish(like, Response.json({ status: 'added', reaction: 'like' }, { status: 201 })); expect(http.unexpected).toEqual([]);
});

it('AB.3 late canonical error after page change cannot publish a stale Retry', async () => {
  const http = blogHTTP(); openBlog(); await settle();
  const accepted = http.hold('POST', reactionPath()), canonical = http.hold('GET', single());
  fireEvent.click(reactionButton()); await finish(accepted, Response.json({ status: 'added', reaction: 'like' }, { status: 201 }));
  fireEvent.click(next()); await settle(); await finish(canonical, Response.json({ detail: INTERNAL }, { status: 503 }));
  expect(screen.getByText(post(10).content)).toBeTruthy(); expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByRole('button', { name: sharedMessages.en.retry })).toBeNull(); expect(http.unexpected).toEqual([]);
});

it('AB.3 newer canonical snapshot wins over older deferred body for independent reactions on one post', async () => {
  const http = blogHTTP(); openBlog(); await settle();
  const like = http.hold('POST', reactionPath()), fire = http.hold('POST', reactionPath()), oldBody = http.holdBody(single());
  fireEvent.click(reactionButton()); await finish(like, Response.json({ status: 'added', reaction: 'like' }, { status: 201 }));
  const newer = http.hold('GET', single()); fireEvent.click(reactionButton(0, 'fire'));
  await finish(fire, Response.json({ status: 'added', reaction: 'fire' }, { status: 201 }));
  await finish(newer, Response.json({ ...post(), reactions: { ...post().reactions, like: 2, fire: 2 } }));
  expect(reactionButton().textContent).toContain('2'); expect(reactionButton().hasAttribute('disabled')).toBe(true);
  await finish(oldBody, { ...post(), reactions: { ...post().reactions, like: 1, fire: 0 } });
  expect(reactionButton().textContent).toContain('2'); expect(reactionButton(0, 'fire').textContent).toContain('2');
  expect(reactionButton().hasAttribute('disabled')).toBe(false); expect(writes(http)).toHaveLength(2); expect(http.unexpected).toEqual([]);
});

it('AB.2 deferred paging JSON keeps both controls locked until the accepted page commits', async () => {
  const http = blogHTTP(); openBlog(); await settle(); const body = http.holdBody(collection(10));
  fireEvent.click(next()); await settle(); expect(screen.getByText(post().content)).toBeTruthy();
  expect(prev().hasAttribute('disabled')).toBe(true); expect(next().hasAttribute('disabled')).toBe(true);
  act(() => { next().click(); prev().click(); });
  expect(reads(http, collection(10))).toHaveLength(1); expect(reads(http, collection())).toHaveLength(1);
  await finish(body, page(10)); expect(screen.getByText(post(10).content)).toBeTruthy(); expect(http.unexpected).toEqual([]);
});
