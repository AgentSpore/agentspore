import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, vi } from 'vitest';
import AgentChatPage from './page';
import { API_URL, type Agent, type DirectMessage } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { communicationMessages } from '@/lib/i18n/communication';
import { sharedMessages } from '@/lib/i18n/shared';

const route = vi.hoisted(() => ({ id: 'agent-a' }));
vi.mock('next/navigation', () => ({ useParams: () => route, usePathname: () => `/agents/${route.id}/chat`, useRouter: () => ({ refresh: vi.fn() }) }));
export type Locale = 'en' | 'ru';
export type Failure = 'http' | 'network';
export const A = { id: 'person-a', name: 'Synthetic A', email: 'a@example.invalid', avatar_url: null, token_balance: 0, is_admin: false };
export const B = { ...A, id: 'person-b', name: 'Synthetic B', email: 'b@example.invalid' };
export const PRIVATE_DETAIL = 'Synthetic private failure';
export const TOKEN_A = 'synthetic-access-a';
export const TOKEN_B = 'synthetic-access-b';
export const disclosure = { en: 'Messages in this chat are public.', ru: 'Сообщения в этом чате видны всем.' };
export const pollCopy = { en: 'Could not refresh messages.', ru: 'Не удалось обновить сообщения.' };
export const copy = (locale: Locale, key: keyof typeof communicationMessages.en) => communicationMessages[locale][key];

export function message(id: string, content = `Message ${id}`): DirectMessage {
  return { id, from_name: 'Synthetic visitor', from_handle: null, sender_type: 'human', content, is_read: false, created_at: '2026-10-20T12:00:00Z' };
}
export const ROWS = Array.from({ length: 50 }, (_, i) => message(`row-${i}`));
export const AGENTS: Record<string, Agent> = Object.fromEntries(['a', 'b'].map(suffix => [`agent-${suffix}`, {
  id: `agent-${suffix}`, name: `Synthetic Agent ${suffix.toUpperCase()}`, handle: `synthetic-${suffix}`,
  agent_type: 'external', model_provider: 'synthetic', model_name: 'synthetic', specialization: 'programmer',
  skills: [], karma: 0, projects_created: 0, code_commits: 0, reviews_done: 0, last_heartbeat: null,
  is_active: true, created_at: '2026-10-20T00:00:00Z', dna_risk: 1, dna_speed: 1,
  dna_verbosity: 1, dna_creativity: 1, bio: null, fork_count: 0, is_hosted: false,
}]));

export function deferred() {
  let resolve!: (response: Response) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<Response>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
export type Pending = ReturnType<typeof deferred>;
type RequestRecord = { path: string; query: string; method: string; headers: Headers; body: string | null };
type ResponseFactory = () => Promise<Response>;
export function boundary() {
  const calls: RequestRecord[] = [], unexpected: string[] = [];
  const plans = new Map<string, ResponseFactory[]>();
  const rows = new Map<string, DirectMessage[]>([['synthetic-a', []], ['synthetic-b', []]]);
  const hold = (key: string) => {
    const pending = deferred();
    const queue = plans.get(key) ?? [];
    queue.push(() => pending.promise.then(response => response.clone()));
    plans.set(key, queue);
    return pending;
  };
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL);
    const method = init?.method ?? (input instanceof Request ? input.method : 'GET');
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    calls.push({ path: url.pathname, query: url.search, method, headers, body: typeof init?.body === 'string' ? init.body : null });
    const profile = url.pathname === '/api/v1/auth/me' && method === 'GET' && !url.search;
    const agent = /^\/api\/v1\/agents\/(agent-[ab])$/.exec(url.pathname);
    const history = /^\/api\/v1\/chat\/dm\/(synthetic-[ab])\/messages$/.exec(url.pathname);
    const post = /^\/api\/v1\/chat\/dm\/(synthetic-[ab])$/.exec(url.pathname);
    let key: string;
    if (profile) key = `profile:${headers.get('Authorization')}`;
    else if (agent && method === 'GET' && !url.search) key = `agent:${agent[1]}`;
    else if (history && method === 'GET' && ['10', '50'].includes(url.searchParams.get('limit') ?? '') && [...url.searchParams.keys()].every(k => k === 'limit' || k === 'before')) {
      key = `${url.searchParams.has('before') ? 'older' : url.searchParams.get('limit') === '10' ? 'poll' : 'initial'}:${history[1]}`;
    } else if (post && method === 'POST' && !url.search) key = `post:${post[1]}`;
    else { unexpected.push(`${method} ${url.pathname}${url.search}`); throw new Error('Unexpected synthetic HTTP request'); }
    const planned = plans.get(key)?.shift();
    if (planned) return planned();
    if (profile) return Response.json(headers.get('Authorization') === `Bearer ${TOKEN_B}` ? B : A);
    if (agent) return Response.json(AGENTS[agent[1]]);
    if (history) return Response.json(rows.get(history[1]) ?? []);
    return Response.json({ status: 'ok' });
  });
  vi.stubGlobal('fetch', fetcher);
  return { calls, unexpected, hold, rows, fetcher };
}
export type Boundary = ReturnType<typeof boundary>;

const originalScroll = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView');
beforeEach(() => {
  route.id = 'agent-a'; localStorage.clear();
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
});
afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
  if (originalScroll) Object.defineProperty(Element.prototype, 'scrollIntoView', originalScroll);
  else Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
});
export function session(token: string | null) {
  if (token) localStorage.setItem('access_token', token);
  else localStorage.removeItem('access_token');
  window.dispatchEvent(new Event('storage'));
}
export function renderChat(locale: Locale = 'en') {
  const tree = () => <LocaleProvider initialLocale={locale}><AgentChatPage /></LocaleProvider>;
  const view = render(tree());
  return { ...view, navigate: async (id: string) => { route.id = id; view.rerender(tree()); await act(async () => {}); } };
}
export async function openChat(http: Boundary, locale: Locale = 'en') {
  const view = renderChat(locale);
  await act(async () => {});
  expect(screen.getByText(AGENTS[route.id].name)).toBeTruthy();
  expect(http.unexpected).toEqual([]);
  return view;
}
export function input() {
  const element = screen.getByRole('textbox');
  if (!(element instanceof HTMLTextAreaElement)) throw new Error('Missing direct chat textarea');
  return element;
}
export function typeDraft(value: string) { fireEvent.change(input(), { target: { value } }); }
export function send() { fireEvent.keyDown(input(), { key: 'Enter', shiftKey: false }); }
export async function finish(pending: Pending, data: unknown) { await act(async () => pending.resolve(Response.json(data))); }
export async function fail(pending: Pending, mode: Failure) {
  await act(async () => {
    if (mode === 'http') pending.resolve(Response.json({ detail: PRIVATE_DETAIL }, { status: 503 }));
    else pending.reject(new TypeError(PRIVATE_DETAIL));
  });
}
export function alertFor(text: string) {
  const element = screen.getByText(text).closest('[role="alert"]');
  if (!(element instanceof HTMLElement)) throw new Error(`Missing scoped alert: ${text}`);
  return element;
}
export function retry(text: string, locale: Locale = 'en') {
  fireEvent.click(within(alertFor(text)).getByRole('button', { name: sharedMessages[locale].retry }));
}
export function reads(http: Boundary, kind = 'initial', handle = 'synthetic-a') {
  return http.calls.filter(call => call.method === 'GET' && call.path === `/api/v1/chat/dm/${handle}/messages` && (kind === 'older' ? call.query.includes('before=') : !call.query.includes('before=') && call.query === `?limit=${kind === 'poll' ? '10' : '50'}`));
}
export function writes(http: Boundary, handle = 'synthetic-a') { return http.calls.filter(call => call.method === 'POST' && call.path === `/api/v1/chat/dm/${handle}`); }
export function assertWrite(http: Boundary, content: string, owner = A, token = TOKEN_A) {
  const request = writes(http).at(-1);
  expect(request?.headers.get('Authorization')).toBe(`Bearer ${token}`);
  expect(request?.headers.get('Content-Type')).toBe('application/json');
  expect(JSON.parse(request?.body ?? '{}')).toEqual({ name: owner.name, content });
}
export function scrollTopTwice() {
  const main = screen.getByRole('main');
  Object.defineProperty(main, 'scrollTop', { configurable: true, value: 0, writable: true });
  act(() => { fireEvent.scroll(main); fireEvent.scroll(main); });
}
export function noRawFailure(http: Boundary) { expect(screen.queryByText(PRIVATE_DETAIL)).toBeNull(); expect(http.unexpected).toEqual([]); }
