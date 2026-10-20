// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import BlogPostPage from './page';
import { API_URL, type BlogComment, type BlogPost } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';

const route = vi.hoisted(() => ({ id: '00000000-0000-4000-8000-0000000000a1' }));
vi.mock('next/navigation', () => ({ useParams: () => route, usePathname: () => `/blog/${route.id}`, useRouter: () => ({ refresh: vi.fn() }) }));
const A = '00000000-0000-4000-8000-0000000000a1', B = '00000000-0000-4000-8000-0000000000b1';
const USER_A = '00000000-0000-4000-8000-0000000000c1', USER_B = '00000000-0000-4000-8000-0000000000d1';
const C1 = '00000000-0000-4000-8000-000000000001', C2 = '00000000-0000-4000-8000-000000000002';
const FOREIGN = '00000000-0000-4000-8000-000000000003', AGENT = '00000000-0000-4000-8000-000000000004';
const SENTINEL = 'Synthetic private comment exception';
type Locale = 'en' | 'ru';
const COPY = {
  en: { post: 'Could not post comment.', remove: 'Could not delete comment.' },
  ru: { post: 'Не удалось отправить комментарий.', remove: 'Не удалось удалить комментарий.' },
};
const postPath = (id = A) => `/api/v1/blog/posts/${id}`;
const commentsPath = (id = A) => `${postPath(id)}/comments`;
const deletePath = (id = C1) => `${commentsPath()}/${id}`;
function post(id = A): BlogPost {
  return { id, agent_id: AGENT, agent_name: 'Synthetic Agent', agent_handle: 'synthetic-agent', title: `Publication ${id}`,
    content: 'Synthetic readable publication.', created_at: '2026-10-20T09:00:00Z', reactions: { like: 0, fire: 0, insightful: 0, funny: 0 } };
}
function comment(id = C1, author = USER_A, postId = A): BlogComment {
  return { id, post_id: postId, author_type: id === AGENT ? 'agent' : 'user', author_id: author,
    author_name: 'Same displayed name', content: `Comment ${id}`, created_at: '2026-10-20T09:00:00Z' };
}
const rows = () => [comment(), comment(C2), comment(FOREIGN, USER_B), comment(AGENT, AGENT)];
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function publicRead(path: string): Response | undefined {
  if ([postPath(A), postPath(B)].includes(path)) return Response.json(post(path === postPath(B) ? B : A));
  if (path === commentsPath(A)) return Response.json(rows());
  if (path === commentsPath(B)) return Response.json([comment(C2, USER_B, B)]);
}
function commentHTTP() {
  const plans = new Map<string, Promise<Response>[]>();
  const calls: { key: string; authorization: string | null; body: BodyInit | null | undefined }[] = [], unexpected: string[] = [];
  const queue = (method: string, path: string, value: Promise<Response>) => {
    const key = `${method} ${path}`; plans.set(key, [...(plans.get(key) ?? []), value]);
  };
  const hold = (method: string, path: string) => { const pending = deferred<Response>(); queue(method, path, pending.promise); return pending; };
  const respond = (method: string, path: string, value: unknown, status = 200) => queue(method, path, Promise.resolve(Response.json(value, { status })));
  const holdBody = (path = commentsPath()) => {
    const body = deferred<unknown>(), response = Response.json(null); response.json = () => body.promise;
    queue('GET', path, Promise.resolve(response)); return body;
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL), method = init?.method ?? 'GET';
    const path = url.pathname + url.search, key = `${method} ${path}`, authorization = new Headers(init?.headers).get('Authorization');
    calls.push({ key, authorization, body: init?.body });
    const planned = plans.get(key)?.shift(); if (planned) return planned;
    if (method === 'GET' && path === '/api/v1/auth/me') return Response.json({ id: authorization?.includes('synthetic-B') ? USER_B : USER_A,
      name: 'Same displayed name', email: 'synthetic@example.invalid', avatar_url: null, token_balance: 0, is_admin: false });
    if (method === 'GET' && !authorization) { const value = publicRead(path); if (value) return value; }
    unexpected.push(key); throw new Error('Unexpected synthetic comment request');
  }));
  return { calls, unexpected, hold, holdBody, respond };
}
type HTTP = ReturnType<typeof commentHTTP>;
const settle = async () => { await act(async () => {}); };
async function finish<T>(pending: ReturnType<typeof deferred<T>>, value: T) { await act(async () => pending.resolve(value)); }
const calls = (http: HTTP, method: string, path = commentsPath()) => http.calls.filter(call => call.key === `${method} ${path}`);
const draft = () => within(screen.getByRole('main')).getByRole('textbox');
const submit = (locale: Locale = 'en') => within(screen.getByRole('main')).getByRole('button', { name: publicMessages[locale].post2 });
const type = (value: string) => fireEvent.change(draft(), { target: { value } });
function row(id = C1) {
  const node = screen.getByText(`Comment ${id}`).parentElement;
  if (!(node instanceof HTMLElement)) throw new Error('Expected comment content container');
  return within(node);
}
const remove = (id = C1, locale: Locale = 'en') => row(id).getByTitle(publicMessages[locale].delete);
function retry(message: string, locale: Locale = 'en') {
  const notice = screen.getByText(message).closest('[role="alert"]');
  if (!(notice instanceof HTMLElement)) throw new Error('Expected scoped comment error');
  return within(notice).getByRole('button', { name: sharedMessages[locale].retry });
}
async function open(locale: Locale = 'en') {
  const scene = () => <LocaleProvider initialLocale={locale}><BlogPostPage /></LocaleProvider>;
  const view = render(scene()); await settle();
  return { ...view, navigate: async () => { route.id = B; view.rerender(scene()); await settle(); } };
}
async function changeAccount(value: string | null) {
  if (value === null) localStorage.removeItem('access_token'); else localStorage.setItem('access_token', value);
  await act(async () => window.dispatchEvent(new Event('storage')));
}
beforeEach(() => { route.id = A; localStorage.clear(); localStorage.setItem('access_token', 'synthetic-A'); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });

it.each(['new', 'ABA'] as const)('BM.C1 held POST is singleflight and preserves the %s draft revision', async variant => {
  const http = commentHTTP(), pending = http.hold('POST', commentsPath()); await open(); type(' submitted text ');
  act(() => { submit().click(); submit().click(); fireEvent.keyDown(draft(), { key: 'Enter', ctrlKey: true }); });
  expect(calls(http, 'POST')).toHaveLength(1);
  expect(calls(http, 'POST')[0]).toEqual({ key: `POST ${commentsPath()}`, authorization: 'Bearer synthetic-A', body: JSON.stringify({ content: 'submitted text' }) });
  type('new draft'); if (variant === 'ABA') type(' submitted text ');
  await finish(pending, Response.json(comment(), { status: 201 }));
  expect(draft()).toHaveProperty('value', variant === 'ABA' ? ' submitted text ' : 'new draft');
  expect(calls(http, 'GET')).toHaveLength(2); expect(calls(http, 'GET', postPath())).toHaveLength(1); expect(http.unexpected).toEqual([]);
});
it('BM.C2 HTTP and network POST failures keep the RU current draft and require explicit current-payload submission', async () => {
  const http = commentHTTP(); await open('ru'); type('first draft');
  http.respond('POST', commentsPath(), { detail: SENTINEL }, 503); fireEvent.click(submit('ru')); await settle();
  expect(screen.getByText(COPY.ru.post)).toBeTruthy(); expect(draft()).toHaveProperty('value', 'first draft');
  expect(screen.queryByText(SENTINEL)).toBeNull(); expect(screen.getByText(`Comment ${C1}`)).toBeTruthy(); expect(calls(http, 'GET')).toHaveLength(1);
  type('second draft'); const network = http.hold('POST', commentsPath()); fireEvent.click(submit('ru'));
  await act(async () => network.reject(new TypeError(SENTINEL)));
  expect(screen.getByText(COPY.ru.post)).toBeTruthy(); expect(draft()).toHaveProperty('value', 'second draft'); expect(calls(http, 'POST')).toHaveLength(2);
  http.respond('POST', commentsPath(), comment(), 201); fireEvent.click(submit('ru')); await settle();
  expect(calls(http, 'POST')[2]?.body).toBe(JSON.stringify({ content: 'second draft' }));
  expect(calls(http, 'POST')).toHaveLength(3); expect(draft()).toHaveProperty('value', ''); expect(http.unexpected).toEqual([]);
});
it('BM.C3 accepted POST then failed canonical refresh offers GET-only Retry', async () => {
  const http = commentHTTP(); await open(); type('accepted text');
  http.respond('POST', commentsPath(), comment(), 201); http.respond('GET', commentsPath(), { detail: SENTINEL }, 503);
  fireEvent.click(submit()); await settle();
  const notice = within(screen.getByRole('main')).getByRole('alert');
  fireEvent.click(within(notice).getByRole('button', { name: sharedMessages.en.retry })); await settle();
  expect(calls(http, 'POST')).toHaveLength(1); expect(calls(http, 'GET')).toHaveLength(3);
  expect(calls(http, 'GET', postPath())).toHaveLength(1); expect(screen.getByText(`Comment ${C1}`)).toBeTruthy();
  expect(draft()).toHaveProperty('value', ''); expect(screen.queryByText(SENTINEL)).toBeNull(); expect(http.unexpected).toEqual([]);
});
it('BM.C4 only confirmed own user rows expose Delete; same-owner checking retains draft and logout masks mutations', async () => {
  const http = commentHTTP(); await open();
  expect(row().queryAllByTitle('Delete')).toHaveLength(1);
  expect(row(FOREIGN).queryByTitle('Delete')).toBeNull(); expect(row(AGENT).queryByTitle('Delete')).toBeNull();
  expect(row().getByRole('button', { name: 'Delete' })).toBeTruthy();
  type('retained across checking');
  const header = http.hold('GET', '/api/v1/auth/me'), identity = http.hold('GET', '/api/v1/auth/me');
  await changeAccount('synthetic-A-refresh');
  expect(row().queryByRole('button', { name: 'Delete' })).toBeNull();
  expect(within(screen.getByRole('main')).queryByRole('button', { name: publicMessages.en.post2 })).toBeNull();
  const profile = { id: USER_A, name: 'Renamed reader', email: 'synthetic@example.invalid', avatar_url: null, token_balance: 0, is_admin: false };
  await finish(header, Response.json(profile)); await finish(identity, Response.json(profile));
  expect(draft()).toHaveProperty('value', 'retained across checking'); expect(remove()).toBeTruthy();
  await changeAccount(null); expect(row().queryByRole('button', { name: 'Delete' })).toBeNull();
  expect(within(screen.getByRole('main')).queryByRole('button', { name: publicMessages.en.post2 })).toBeNull(); expect(http.unexpected).toEqual([]);
});
it('BM.C5 old account completion cannot clear B draft, refresh its rows or release B write lock', async () => {
  const http = commentHTTP(), old = http.hold('POST', commentsPath()); await open(); type('A draft'); fireEvent.click(submit());
  await changeAccount('synthetic-B'); expect(draft()).toHaveProperty('value', '');
  const current = http.hold('POST', commentsPath()); type('B draft'); fireEvent.click(submit());
  await finish(old, Response.json(comment(), { status: 201 }));
  expect(draft()).toHaveProperty('value', 'B draft'); expect(calls(http, 'GET')).toHaveLength(1);
  fireEvent.keyDown(draft(), { key: 'Enter', metaKey: true }); expect(calls(http, 'POST')).toHaveLength(2);
  await finish(current, Response.json(comment(C2, USER_B), { status: 201 }));
  expect(draft()).toHaveProperty('value', ''); expect(calls(http, 'POST')[1]?.authorization).toBe('Bearer synthetic-B');
  expect(calls(http, 'GET')).toHaveLength(2); expect(http.unexpected).toEqual([]);
});
it('BM.C5 replaced route and unmounted write cannot refresh another context', async () => {
  const http = commentHTTP(), old = http.hold('POST', commentsPath()), view = await open(); type('old A draft'); fireEvent.click(submit());
  await view.navigate(); expect(draft()).toHaveProperty('value', '');
  const before = http.calls.length; await finish(old, Response.json(comment(), { status: 201 }));
  expect(http.calls).toHaveLength(before); expect(screen.queryByText(`Comment ${C1}`)).toBeNull();
  const current = http.hold('POST', commentsPath(B)); type('B draft'); fireEvent.click(submit()); view.unmount();
  const atUnmount = http.calls.length; await finish(current, Response.json(comment(C2, USER_A, B), { status: 201 }));
  expect(http.calls).toHaveLength(atUnmount); expect(http.unexpected).toEqual([]);
});
it('BM.C6 same-row Delete is singleflight while independent rows progress separately', async () => {
  const http = commentHTTP(), first = http.hold('DELETE', deletePath()), second = http.hold('DELETE', deletePath(C2)); await open();
  act(() => { remove().click(); remove().click(); }); expect(calls(http, 'DELETE', deletePath())).toHaveLength(1);
  fireEvent.click(remove(C2)); expect(calls(http, 'DELETE', deletePath(C2))).toHaveLength(1);
  await finish(first, Response.json({ status: 'deleted' })); expect(screen.queryByText(`Comment ${C1}`)).toBeNull();
  expect(screen.getByText(`Comment ${C2}`)).toBeTruthy(); expect(remove(C2)).toHaveProperty('disabled', true);
  await finish(second, Response.json({ status: 'deleted' })); expect(screen.queryByText(`Comment ${C2}`)).toBeNull();
  expect(screen.getByText(`Comment ${FOREIGN}`)).toBeTruthy(); expect(calls(http, 'GET')).toHaveLength(1); expect(http.unexpected).toEqual([]);
});
it('BM.C6 failed DELETE retains its row and explicit scoped Retry performs DELETE only', async () => {
  const http = commentHTTP(); await open(); http.respond('DELETE', deletePath(), { detail: SENTINEL }, 404);
  fireEvent.click(remove()); await settle(); expect(screen.getByText(COPY.en.remove)).toBeTruthy();
  expect(screen.getByText(`Comment ${C1}`)).toBeTruthy(); expect(screen.queryByText(SENTINEL)).toBeNull();
  const network = http.hold('DELETE', deletePath()); fireEvent.click(retry(COPY.en.remove));
  await act(async () => network.reject(new TypeError(SENTINEL)));
  expect(screen.getByText(COPY.en.remove)).toBeTruthy(); expect(screen.getByText(`Comment ${C1}`)).toBeTruthy();
  http.respond('DELETE', deletePath(), { status: 'deleted' }); fireEvent.click(retry(COPY.en.remove)); await settle();
  expect(screen.queryByText(`Comment ${C1}`)).toBeNull(); expect(calls(http, 'DELETE', deletePath())).toHaveLength(3);
  expect(calls(http, 'GET')).toHaveLength(1); expect(calls(http, 'POST')).toHaveLength(0); expect(http.unexpected).toEqual([]);
});
it('BM.C6 accepted DELETE survives an overlapping canonical comment body without resurrecting its row', async () => {
  const http = commentHTTP(); await open(); type('start canonical refresh');
  http.respond('POST', commentsPath(), comment(), 201); const oldBody = http.holdBody(), submittedButton = submit(); fireEvent.click(submittedButton); await settle();
  type('next draft'); expect(submittedButton).toHaveProperty('disabled', true);
  http.respond('DELETE', deletePath(), { status: 'deleted' }); fireEvent.click(remove()); await settle();
  expect(screen.queryByText(`Comment ${C1}`)).toBeNull(); await finish(oldBody, rows());
  expect(submittedButton).toHaveProperty('disabled', false); expect(draft()).toHaveProperty('value', 'next draft');
  expect(screen.queryByText(`Comment ${C1}`)).toBeNull(); expect(screen.getByText(`Comment ${FOREIGN}`)).toBeTruthy();
  expect(calls(http, 'DELETE', deletePath())).toHaveLength(1); expect(calls(http, 'GET')).toHaveLength(2); expect(http.unexpected).toEqual([]);
});
