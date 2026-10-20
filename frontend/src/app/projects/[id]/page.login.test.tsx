// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProjectPage from './page';
import { API_URL, type ProjectOwnership } from '@/lib/api';
import { type Project } from './useProjectReads';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';
const route = vi.hoisted(() => ({ id: 'synthetic-a' }));
vi.mock('next/navigation', () => ({ useParams: () => route, usePathname: () => `/projects/${route.id}`, useRouter: () => ({ refresh: vi.fn() }) }));
type Locale = 'en' | 'ru';
const NOW = '2026-10-20T12:00:00Z', INTERNAL = 'Synthetic internal login failure';
const EMAIL = 'modal-b@example.invalid', PASSWORD = 'SyntheticPassword7';
const COPY = { en: { generic: 'Could not sign in. Try again.', wait: 'Too many attempts. Try again later.', verify: 'Verify your email before signing in.' },
  ru: { generic: 'Не удалось войти. Попробуйте ещё раз.', wait: 'Слишком много попыток. Попробуйте войти позже.', verify: 'Подтвердите email перед входом.' } };
const PROJECT: Project = { id: 'synthetic-a', title: 'Synthetic project A', description: 'Synthetic overview', category: 'tools', status: 'active',
  repo_url: null, deploy_url: null, tech_stack: [], agent_name: 'Synthetic agent', agent_handle: 'synthetic-agent', creator_agent_id: 'synthetic-agent', votes_up: 1, votes_down: 0, created_at: NOW };
const path = (id = PROJECT.id) => `/api/v1/projects/${id}`;
function pair(owner = 'b', base64url = false) {
  const payload = btoa(JSON.stringify(base64url ? { sub: `synthetic-${owner}-user`, probe: '??>' } : { sub: `synthetic-${owner}-user` })).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return { access_token: `${btoa('{"alg":"none"}')}.${payload}.synthetic`, refresh_token: `synthetic-${owner}-refresh`, token_type: 'bearer' };
}
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject };
}
function loginRequest(input: RequestInfo | URL, init?: RequestInit) {
  const url = new URL(input instanceof Request ? input.url : String(input), API_URL);
  return { path: url.pathname + url.search, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : null,
    authorization: new Headers(init?.headers).get('Authorization') };
}
function loginHTTP() {
  const bodies = new Map<string, unknown>();
  for (const id of [PROJECT.id, 'synthetic-b']) {
    const ownership: ProjectOwnership = { project_id: id, project_title: PROJECT.title, contributors: [] };
    bodies.set(path(id), { ...PROJECT, id, title: id === PROJECT.id ? PROJECT.title : 'Synthetic project B' });
    bodies.set(`${path(id)}/ownership`, ownership); bodies.set(`${path(id)}/contributors`, { contributors: [] });
  }
  const plans: Promise<Response>[] = [], calls: { path: string; method: string; body: unknown; authorization: string | null }[] = [], unexpected: string[] = [];
  const hold = () => { const pending = deferred<Response>(); plans.push(pending.promise); return pending; };
  const holdBody = () => { const pending = deferred<unknown>(), response = Response.json(null); response.json = () => pending.promise; plans.push(Promise.resolve(response)); return pending; };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = loginRequest(input, init), { path: requestPath, method, authorization } = call; calls.push(call);
    if (method === 'POST' && requestPath === '/api/v1/auth/login') {
      const response = plans.shift(); if (response) return response;
    }
    if (method === 'POST' && requestPath === `${path()}/contributors/join`) return Response.json({ detail: INTERNAL }, { status: 401 });
    if (method === 'GET') {
      if (requestPath === '/api/v1/auth/me') {
        const owner = authorization === `Bearer ${pair('a').access_token}` ? 'a' : 'b';
        return Response.json({ id: `synthetic-${owner}-user`, email: `${owner}@example.invalid`, name: `Synthetic account ${owner.toUpperCase()}`, avatar_url: null, token_balance: 0, is_admin: false });
      }
      if (bodies.has(requestPath)) return Response.json(bodies.get(requestPath));
    }
    unexpected.push(`${method} ${requestPath}`); throw new Error('Unexpected synthetic login request');
  }));
  return { calls, unexpected, hold, holdBody };
}
const settle = async () => { await act(async () => {}); };
async function finish<T>(pending: ReturnType<typeof deferred<T>>, value: T) { await act(async () => pending.resolve(value)); }
function install(owner = 'a') {
  const tokens = pair(owner); localStorage.setItem('access_token', tokens.access_token); localStorage.setItem('refresh_token', tokens.refresh_token);
  localStorage.setItem('auth', JSON.stringify({ token: tokens.access_token, email: `${owner}@example.invalid`, userId: `synthetic-${owner}-user` }));
  window.dispatchEvent(new Event('storage'));
}
const stored = () => ({ auth: localStorage.getItem('auth'), access: localStorage.getItem('access_token'), refresh: localStorage.getItem('refresh_token') });
function modal(locale: Locale = 'en') {
  const heading = screen.getByRole('heading', { name: publicMessages[locale].signInToContinue });
  const container = heading.closest('dialog') ?? heading.parentElement?.parentElement;
  if (!(container instanceof HTMLElement)) throw new Error('Missing project login modal'); return within(container);
}
function form(locale: Locale = 'en') {
  const field = modal(locale).getByPlaceholderText(publicMessages[locale].email), element = field.closest('form');
  if (!(element instanceof HTMLFormElement)) throw new Error('Missing project login form'); return element;
}
function fill(locale: Locale = 'en') {
  fireEvent.change(modal(locale).getByPlaceholderText(publicMessages[locale].email), { target: { value: EMAIL } });
  fireEvent.change(modal(locale).getByPlaceholderText(publicMessages[locale].password), { target: { value: PASSWORD } });
}
const submit = (locale: Locale = 'en') => fireEvent.submit(form(locale));
const close = (locale: Locale = 'en') => fireEvent.click(modal(locale).getByRole('button', { name: /^(x|close|закрыть)$/ }));
const posts = (http: ReturnType<typeof loginHTTP>) => http.calls.filter(call => call.path === '/api/v1/auth/login');
function show(locale: Locale = 'en') { fireEvent.click(screen.getByRole('button', { name: publicMessages[locale].signIn2 })); }
function openProject(locale: Locale = 'en') {
  const tree = () => <LocaleProvider initialLocale={locale}><ProjectPage /></LocaleProvider>, view = render(tree());
  return { ...view, navigate: async () => { route.id = 'synthetic-b'; view.rerender(tree()); await settle(); } };
}
async function openUnderAccount() {
  install(); const view = openProject(); await settle(); fireEvent.click(screen.getByRole('button', { name: /^Contributors/ }));
  fireEvent.click(screen.getByRole('button', { name: publicMessages.en.requestToJoin })); await settle();
  fireEvent.click(screen.getByRole('button', { name: publicMessages.en.projectJoinSignInAgain })); return view;
}
function dialogPlatform() {
  const prototype = HTMLDialogElement.prototype, descriptors = ['showModal', 'close'].map(key => [key, Object.getOwnPropertyDescriptor(prototype, key)] as const);
  Object.defineProperty(prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.setAttribute('open', ''); } });
  Object.defineProperty(prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.removeAttribute('open'); this.dispatchEvent(new Event('close')); } });
  return () => { for (const [key, descriptor] of descriptors) { if (descriptor) Object.defineProperty(prototype, key, descriptor); else Reflect.deleteProperty(prototype, key); } };
}
let restoreDialog = () => {};
beforeEach(() => { restoreDialog = dialogPlatform(); route.id = PROJECT.id; localStorage.clear(); vi.useFakeTimers(); vi.setSystemTime(new Date(NOW)); });
afterEach(() => { cleanup(); restoreDialog(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each(['anonymous', 'previous account', 'base64url'] as const)('PL1 successful modal login replaces all session projections from %s', async mode => {
  const http = loginHTTP(); if (mode === 'previous account') await openUnderAccount(); else { openProject(); await settle(); show(); }
  fill(); const pending = http.hold(), tokens = pair('b', mode === 'base64url'); submit(); expect(stored().access).toBe(mode === 'previous account' ? pair('a').access_token : null);
  await finish(pending, Response.json(tokens));
  expect(posts(http)).toEqual([{ path: '/api/v1/auth/login', method: 'POST', body: { email: EMAIL, password: PASSWORD }, authorization: null }]);
  expect(stored()).toEqual({ auth: JSON.stringify({ token: tokens.access_token, email: EMAIL, userId: 'synthetic-b-user' }), access: tokens.access_token, refresh: tokens.refresh_token });
  expect(screen.queryByRole('heading', { name: publicMessages.en.signInToContinue })).toBeNull();
  expect(screen.getByRole('button', { name: /Synthetic account B/ })).toBeTruthy();
  expect(http.calls.filter(call => call.path === `${path()}/contributors`).at(-1)?.authorization).toBe(`Bearer ${tokens.access_token}`); expect(http.unexpected).toEqual([]);
});
it('PL1 synchronous repeated submissions acquire exactly one request', async () => {
  const http = loginHTTP(); openProject(); await settle(); show(); fill(); const pending = http.hold(), element = form();
  act(() => { fireEvent.submit(element); fireEvent.submit(element); fireEvent.submit(element); }); expect(posts(http)).toHaveLength(1);
  await finish(pending, Response.json(pair())); expect(http.unexpected).toEqual([]);
});
it.each([{ locale: 'en', status: 401 }, { locale: 'ru', status: 403 }, { locale: 'en', status: 429 }, { locale: 'ru', status: 503 }, { locale: 'en', status: 201 }] as const)(
  'PL2 $locale HTTP$status shows its localized outcome without automatic replay', async ({ locale, status }) => {
    const http = loginHTTP(); openProject(locale); await settle(); show(locale); fill(locale); const pending = http.hold(); submit(locale);
    await finish(pending, Response.json(status === 201 ? pair() : { detail: INTERNAL }, { status }));
    const expected = status === 401 ? publicMessages[locale].invalidEmailOrPassword : status === 403 ? COPY[locale].verify : status === 429 ? COPY[locale].wait : COPY[locale].generic;
    expect(modal(locale).getByRole('alert').textContent).toContain(expected); expect(screen.queryByText(INTERNAL)).toBeNull(); expect(stored()).toEqual({ auth: null, access: null, refresh: null });
    await act(async () => vi.advanceTimersByTimeAsync(60000)); expect(posts(http)).toHaveLength(1);
    const next = http.hold(); submit(locale); await finish(next, Response.json(pair())); expect(posts(http)).toHaveLength(2); expect(http.unexpected).toEqual([]);
  });
it('PL2 network failure preserves fields and exposes existing connection copy for explicit retry', async () => {
  const http = loginHTTP(); openProject('ru'); await settle(); show('ru'); fill('ru'); const pending = http.hold(); submit('ru');
  await act(async () => pending.reject(new TypeError(INTERNAL))); expect(modal('ru').getByRole('alert').textContent).toContain(publicMessages.ru.connectionError);
  expect(modal('ru').getByPlaceholderText(publicMessages.ru.email).getAttribute('value')).toBe(EMAIL);
  expect(modal('ru').getByRole('button', { name: publicMessages.ru.signIn }).hasAttribute('disabled')).toBe(false); expect(posts(http)).toHaveLength(1);
});
it.each(['refresh', 'sub', 'base64', 'json'] as const)('PL2 malformed %s success cannot install any session', async mode => {
  const http = loginHTTP(); openProject(); await settle(); show(); fill(); const pending = http.hold(); submit();
  const invalid = mode === 'refresh' ? { access_token: pair().access_token } : mode === 'sub' ? { ...pair(), access_token: `${btoa('{}')}.${btoa('{"sub":""}')}.synthetic` } : { ...pair(), access_token: 'synthetic.!.signature' };
  await finish(pending, mode === 'json' ? new Response('{', { status: 200 }) : Response.json(invalid));
  expect(modal().getByRole('alert').textContent).toContain(COPY.en.generic); expect(stored()).toEqual({ auth: null, access: null, refresh: null });
  expect(modal().getByRole('button', { name: publicMessages.en.signIn }).hasAttribute('disabled')).toBe(false); expect(http.unexpected).toEqual([]);
});
it.each(['response', 'body', 'error'] as const)('PL3 closed modal late %s cannot write storage or unlock reopened login', async mode => {
  const http = loginHTTP(); openProject(); await settle(); show(); fill(); const oldBody = mode === 'body' ? http.holdBody() : null, old = mode === 'body' ? null : http.hold(); submit(); await settle(); close(); show(); fill();
  const current = http.hold(); submit();
  if (oldBody) await finish(oldBody, pair('a'));
  if (old) await act(async () => mode === 'error' ? old.reject(new TypeError(INTERNAL)) : old.resolve(Response.json(pair('a'))));
  expect(stored()).toEqual({ auth: null, access: null, refresh: null }); expect(modal().queryByRole('alert')).toBeNull();
  expect(modal().getByRole('button', { name: publicMessages.en.signingIn }).hasAttribute('disabled')).toBe(true);
  submit(); expect(posts(http)).toHaveLength(2); await finish(current, Response.json(pair())); expect(stored().access).toBe(pair().access_token);
});
it.each(['legacy', 'canonical', 'generation'] as const)('PL3 held JSON after external %s replacement cannot overwrite current owner', async mode => {
  const http = loginHTTP(); openProject(); await settle(); show(); fill(); const old = http.holdBody(); submit(); await settle();
  await act(async () => {
    if (mode === 'legacy') localStorage.setItem('auth', JSON.stringify({ token: pair('a').access_token, email: 'a@example.invalid', userId: 'synthetic-a-user' }));
    else if (mode === 'canonical') install('a'); else localStorage.setItem('refresh_token', 'synthetic-external-refresh');
    window.dispatchEvent(new Event('storage'));
  });
  const currentOwner = stored(); await finish(old, pair()); expect(stored()).toEqual(currentOwner);
  expect(modal().queryByRole('alert')).toBeNull(); expect(modal().getByRole('button', { name: publicMessages.en.signIn }).hasAttribute('disabled')).toBe(false);
  expect(posts(http)).toHaveLength(1); expect(http.unexpected).toEqual([]);
});
it.each(['route', 'unmount'] as const)('PL3 %s replacement rejects late login body storage effects', async mode => {
  const http = loginHTTP(), view = openProject(); await settle(); show(); fill(); const old = http.holdBody(); submit(); await settle();
  if (mode === 'route') await view.navigate(); else view.unmount(); await finish(old, pair());
  expect(stored()).toEqual({ auth: null, access: null, refresh: null }); expect(posts(http)).toHaveLength(1); expect(http.unexpected).toEqual([]);
});
it('PL4 failed refresh-token storage restores the prior complete session without partial modal login', async () => {
  const http = loginHTTP(); await openUnderAccount(); fill(); const before = stored(), pending = http.hold(); submit();
  const original = Storage.prototype.setItem; let failed = false;
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key: string, value: string) {
    if (key === 'refresh_token' && value === pair().refresh_token && !failed) { failed = true; throw new DOMException('Synthetic storage quota', 'QuotaExceededError'); }
    original.call(this, key, value);
  });
  await finish(pending, Response.json(pair())); expect(stored()).toEqual(before); expect(modal().getByRole('alert').textContent).toContain(COPY.en.generic);
  expect(screen.getByRole('button', { name: /Synthetic account A/ })).toBeTruthy(); expect(http.unexpected).toEqual([]);
});
it.each(['en', 'ru'] as const)('PL5 %s modal exposes named dialog, fields and close action', async locale => {
  const http = loginHTTP(); openProject(locale); await settle(); show(locale);
  const dialog = screen.getByRole('dialog', { name: publicMessages[locale].signInToContinue });
  expect(within(dialog).getByRole('textbox', { name: publicMessages[locale].email })).toBeTruthy();
  expect(within(dialog).getByLabelText(publicMessages[locale].password)).toBeTruthy();
  fireEvent.click(within(dialog).getByRole('button', { name: sharedMessages[locale].close }));
  expect(screen.queryByRole('dialog')).toBeNull(); expect(posts(http)).toHaveLength(0); expect(http.unexpected).toEqual([]);
});
