// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProjectPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { communicationMessages } from '@/lib/i18n/communication';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';

// Next routing is the outward browser boundary; account hooks and Header remain real.
vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'demo-project' }),
  usePathname: () => '/projects/demo-project', useRouter: () => ({ refresh: vi.fn() }) }));
beforeEach(() => { localStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

type Operation = 'initial' | 'older' | 'post' | 'patch' | 'delete';
type Locale = 'en' | 'ru';
const BASE = '/api/v1/chat/project/demo-project';
const SENTINEL = 'Synthetic private failure sentinel';
const DRAFT = 'Synthetic retained composer draft';
const PROFILE = { id: 'account-a', name: 'Synthetic owner', email: 'owner@example.invalid',
  avatar_url: null, token_balance: 0, is_admin: false };
const PROJECT = { id: 'demo-project', title: 'Example Project', description: 'Synthetic fixture',
  category: 'tools', status: 'active', repo_url: null, deploy_url: null, tech_stack: [],
  agent_name: 'Demo', agent_handle: 'demo', creator_agent_id: 'demo-agent', votes_up: 0,
  votes_down: 0, created_at: '2026-10-01T00:00:00Z' };
const ROWS = Array.from({ length: 50 }, (_, i) => ({ id: `error-row-${i}`,
  sender_user_id: PROFILE.id, sender_name: PROFILE.name, sender_handle: null,
  sender_type: 'human', content: `Synthetic history ${i}`, message_type: 'text',
  created_at: `2026-10-19T09:${String(i).padStart(2, '0')}:00Z` }));
const OLDER = { ...ROWS[0], id: 'error-row-older', content: 'Synthetic older history',
  created_at: '2026-10-19T08:59:00Z' };
const ERROR_KEYS = { initial: 'Could not load messages.', older: 'Could not load older messages.',
  post: 'Failed to send', patch: 'Failed to edit', delete: 'Could not delete message.' } as const;

function pendingResponse() {
  let resolve: (response: Response) => void = () => { throw new Error('Pending response missing'); };
  let reject: (error: Error) => void = () => { throw new Error('Pending response missing'); };
  const promise = new Promise<Response>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function targetRequest(operation: Operation) {
  if (operation === 'initial') return { method: 'GET', path: `${BASE}/messages?limit=50` };
  if (operation === 'older') return { method: 'GET', path: `${BASE}/messages?limit=50&before=error-row-0` };
  return { method: operation.toUpperCase(), path: `${BASE}/human-messages${operation === 'post' ? '' : '/error-row-0'}` };
}

function stubProjectErrors(operation: Operation) {
  const requests: { method: string; path: string; authorization: string | null; body: unknown }[] = [];
  const pending: ReturnType<typeof pendingResponse>[] = [], unexpected: string[] = [];
  const rows = operation === 'older' ? ROWS : ROWS.slice(0, 2);
  const target = targetRequest(operation);
  localStorage.setItem('access_token', 'synthetic-chat-errors');
  localStorage.setItem('auth', JSON.stringify({ token: 'synthetic-chat-errors', email: PROFILE.email, userId: PROFILE.id }));
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => {
    const url = new URL(input), path = url.pathname + url.search, method = init?.method ?? 'GET';
    if (path === target.path && method === target.method) {
      requests.push({ method, path, authorization: new Headers(init?.headers).get('Authorization'),
        body: init?.body ? JSON.parse(String(init.body)) : null });
      const response = pendingResponse(); pending.push(response); return response.promise;
    }
    if (method === 'GET' && path === `${BASE}/messages?limit=50`) return Promise.resolve(Response.json([...rows].reverse()));
    const known = { '/api/v1/auth/me': PROFILE, '/api/v1/projects/demo-project': PROJECT,
      '/api/v1/projects/demo-project/ownership': null,
      '/api/v1/projects/demo-project/contributors': { contributors: [] } };
    if (method === 'GET' && Object.hasOwn(known, path)) {
      const body = Object.entries(known).find(([key]) => key === path)?.[1];
      return Promise.resolve(Response.json(body));
    }
    unexpected.push(`${method} ${path}`);
    return Promise.reject(new Error('Unexpected synthetic HTTP request'));
  }));
  return { requests, pending, rows, unexpected };
}

async function mountProjectErrors(locale: Locale) {
  render(<LocaleProvider initialLocale={locale}><ProjectPage /></LocaleProvider>);
  await screen.findByRole('heading', { name: PROJECT.title });
  fireEvent.click(screen.getByRole('button', { name: publicMessages[locale].discussion }));
  await screen.findByRole('textbox');
}

async function openProjectErrors(operation: Operation, locale: Locale) {
  const http = stubProjectErrors(operation);
  await mountProjectErrors(locale);
  if (operation !== 'initial') await screen.findByText(ROWS[0].content);
  return http;
}

function ownedRow() {
  const row = screen.getByText(ROWS[0].content).closest('.group');
  if (!(row instanceof HTMLElement)) throw new Error('Synthetic message row missing');
  return row;
}

function changeDraft() {
  const input = screen.getByRole<HTMLTextAreaElement>('textbox');
  fireEvent.change(input, { target: { value: DRAFT } });
  return input;
}

async function startOperation(operation: Operation, locale: Locale) {
  if (operation === 'patch') fireEvent.click(within(ownedRow()).getByTitle(locale === 'en' ? 'Edit' : 'Изменить'));
  const input = changeDraft();
  if (operation === 'older') {
    const panel = ownedRow().closest('.chat-panel');
    if (!(panel instanceof HTMLElement)) throw new Error('Project history panel missing');
    fireEvent.scroll(panel, { target: { scrollTop: 0 } });
  } else if (operation === 'delete') {
    const button = within(ownedRow()).getByTitle(locale === 'en' ? 'Delete' : 'Удалить');
    act(() => { fireEvent.click(button); fireEvent.click(button); });
  } else if (operation === 'post' || operation === 'patch') {
    await act(async () => fireEvent.keyDown(input, { key: 'Enter' }));
  }
}

function assertRequests(requests: ReturnType<typeof stubProjectErrors>['requests'], operation: Operation, count: number) {
  const target = targetRequest(operation);
  const body = operation === 'post' ? { name: PROFILE.name, content: DRAFT, message_type: 'text' }
    : operation === 'patch' ? { content: DRAFT } : null;
  expect(requests).toEqual(Array.from({ length: count }, () => ({ ...target,
    authorization: operation === 'initial' || operation === 'older' ? null : 'Bearer synthetic-chat-errors', body })));
}

async function failOperation(http: ReturnType<typeof stubProjectErrors>, failure: 'http' | 'network') {
  expect(http.pending).toHaveLength(1);
  await act(async () => {
    if (failure === 'http') http.pending[0].resolve(Response.json({ detail: SENTINEL }, { status: 503 }));
    else http.pending[0].reject(new TypeError(SENTINEL));
  });
}

function assertRetained(http: ReturnType<typeof stubProjectErrors>, operation: Operation, locale: Locale) {
  expect(screen.getByRole<HTMLTextAreaElement>('textbox').value).toBe(DRAFT);
  if (operation !== 'initial') for (const row of http.rows) expect(screen.getByText(row.content)).toBeTruthy();
  if (operation === 'initial') expect(screen.queryByText(publicMessages[locale].noMessagesYet)).toBeNull();
  if (operation === 'patch') expect(screen.getByText(publicMessages[locale].editingMessage)).toBeTruthy();
  expect(screen.queryByText(new RegExp(SENTINEL))).toBeNull();
}

async function retryOperation(operation: Operation, alert: HTMLElement, locale: Locale) {
  if (operation === 'post' || operation === 'patch') {
    await act(async () => fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' }));
  } else {
    fireEvent.click(within(alert).getByRole('button', { name: sharedMessages[locale].retry }));
  }
}

async function completeRetry(http: ReturnType<typeof stubProjectErrors>, operation: Operation) {
  expect(http.pending).toHaveLength(2);
  const body = operation === 'initial' ? [...http.rows].reverse()
    : operation === 'older' ? [OLDER] : { status: 'ok', message_id: ROWS[0].id };
  await act(async () => http.pending[1].resolve(Response.json(body)));
}

function assertRecovered(http: ReturnType<typeof stubProjectErrors>, operation: Operation) {
  if (operation === 'post' || operation === 'patch') expect(screen.getByRole<HTMLTextAreaElement>('textbox').value).toBe('');
  else expect(screen.getByRole<HTMLTextAreaElement>('textbox').value).toBe(DRAFT);
  if (operation === 'patch') {
    expect(screen.getByText(DRAFT).closest('.group')).toBeTruthy();
    expect(screen.queryByText(ROWS[0].content)).toBeNull();
  } else if (operation === 'delete') {
    expect(screen.queryByText(ROWS[0].content)).toBeNull();
    expect(screen.getByText('[deleted]')).toBeTruthy();
  } else for (const row of http.rows) expect(screen.getByText(row.content)).toBeTruthy();
  if (operation === 'older') expect(screen.getByText(OLDER.content)).toBeTruthy();
  expect(screen.getByText(ROWS[1].content)).toBeTruthy();
  expect(http.unexpected).toEqual([]);
}

const cases: { operation: Operation; locale: Locale; failure: 'http' | 'network' }[] = [];
for (const operation of ['initial', 'older', 'post', 'patch', 'delete'] as const) {
  for (const locale of ['en', 'ru'] as const) {
    for (const failure of ['http', 'network'] as const) cases.push({ operation, locale, failure });
  }
}

it.each(cases)('CH.2q $operation $failure retains state and retries in $locale', async ({ operation, locale, failure }) => {
  const http = await openProjectErrors(operation, locale);
  await startOperation(operation, locale);
  await waitFor(() => expect(http.pending).toHaveLength(1));
  assertRequests(http.requests, operation, 1);
  await failOperation(http, failure);
  const message = communicationMessages[locale][ERROR_KEYS[operation]];
  const text = await screen.findByText(message);
  const alert = text.closest('[role="alert"]');
  if (!(alert instanceof HTMLElement)) throw new Error('Project operation alert missing');
  assertRetained(http, operation, locale);
  await retryOperation(operation, alert, locale);
  await waitFor(() => expect(http.pending).toHaveLength(2));
  assertRequests(http.requests, operation, 2);
  await completeRetry(http, operation);
  await waitFor(() => expect(screen.queryByText(message)).toBeNull());
  assertRecovered(http, operation);
});

it('CH.2q protection initial pending is not confirmed empty; successful empty is', async () => {
  const http = await openProjectErrors('initial', 'en'); changeDraft();
  expect(screen.queryByText(publicMessages.en.noMessagesYet)).toBeNull();
  await act(async () => http.pending[0].resolve(Response.json([])));
  expect(screen.getByText(publicMessages.en.noMessagesYet)).toBeTruthy();
  expect(screen.getByRole<HTMLTextAreaElement>('textbox').value).toBe(DRAFT);
  assertRequests(http.requests, 'initial', 1);
});

it.each(['success', 'failure'])('CH.2q protection ignores old initial %s after leaving Discussion', async outcome => {
  const http = await openProjectErrors('initial', 'en');
  fireEvent.click(screen.getByRole('button', { name: publicMessages.en.overview }));
  await act(async () => {
    if (outcome === 'success') http.pending[0].resolve(Response.json([{ ...ROWS[0], content: 'Stale old row' }]));
    else http.pending[0].reject(new TypeError(SENTINEL));
  });
  fireEvent.click(screen.getByRole('button', { name: publicMessages.en.discussion }));
  await waitFor(() => expect(http.pending).toHaveLength(2));
  expect(screen.queryByText('Stale old row')).toBeNull();
  expect(screen.queryByText(communicationMessages.en['Could not load messages.'])).toBeNull();
  await act(async () => http.pending[1].resolve(Response.json([...http.rows].reverse())));
  expect(screen.getByText(ROWS[0].content)).toBeTruthy();
  assertRequests(http.requests, 'initial', 2);
});

it('CH.2q protection old initial GET cannot overwrite same-owner replacement history', async () => {
  const http = await openProjectErrors('initial', 'en');
  await act(async () => {
    localStorage.setItem('access_token', 'synthetic-errors-refreshed');
    localStorage.setItem('auth', JSON.stringify({ token: 'synthetic-errors-refreshed', email: PROFILE.email, userId: PROFILE.id }));
    window.dispatchEvent(new Event('storage'));
  });
  await waitFor(() => expect(http.pending).toHaveLength(2));
  await act(async () => http.pending[1].resolve(Response.json([{ ...ROWS[0], content: 'Current generation row' }])));
  await act(async () => http.pending[0].resolve(Response.json([{ ...ROWS[0], content: 'Stale generation row' }])));
  expect(screen.getByText('Current generation row')).toBeTruthy();
  expect(screen.queryByText('Stale generation row')).toBeNull();
  expect(screen.queryByText(communicationMessages.en['Could not load messages.'])).toBeNull();
  expect(http.requests).toHaveLength(2);
});

it('CH.2q protection accepted POST refresh failure retries history without resending POST', async () => {
  const http = await openProjectErrors('initial', 'en');
  await act(async () => http.pending[0].resolve(Response.json([...http.rows].reverse())));
  const read = fetch, post = vi.fn((input: string, init?: RequestInit) => {
    expect(new URL(input).pathname).toBe(`${BASE}/human-messages`);
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer synthetic-chat-errors');
    expect(JSON.parse(String(init?.body))).toEqual({ name: PROFILE.name, content: DRAFT, message_type: 'text' });
    return Promise.resolve(Response.json({ status: 'ok' }));
  });
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => init?.method === 'POST' ? post(input, init) : read(input, init)));
  changeDraft(); await act(async () => fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' }));
  await waitFor(() => expect(http.pending).toHaveLength(2));
  await act(async () => http.pending[1].resolve(Response.json({ detail: SENTINEL }, { status: 503 })));
  const error = screen.getByText(communicationMessages.en['Could not load messages.']);
  expect(screen.queryByText(communicationMessages.en['Failed to send'])).toBeNull();
  expect(screen.getByRole<HTMLTextAreaElement>('textbox').value).toBe('');
  const alert = error.closest('[role="alert"]'); if (!(alert instanceof HTMLElement)) throw new Error('Read alert missing');
  fireEvent.click(within(alert).getByRole('button', { name: sharedMessages.en.retry }));
  await waitFor(() => expect(http.pending).toHaveLength(3));
  await act(async () => http.pending[2].resolve(Response.json([...http.rows].reverse())));
  expect(post).toHaveBeenCalledTimes(1); assertRequests(http.requests, 'initial', 3);
});

it('CH.2q protection accepted POST during pending initial GET must schedule fresh history', async () => {
  const http = await openProjectErrors('initial', 'en');
  const read = fetch, post = vi.fn(() => Promise.resolve(Response.json({ status: 'ok' })));
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => init?.method === 'POST' ? post() : read(input, init)));
  changeDraft(); await act(async () => fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' }));
  expect(post).toHaveBeenCalledTimes(1);
  await act(async () => http.pending[0].resolve(Response.json([...http.rows].reverse())));
  await waitFor(() => expect(http.pending).toHaveLength(2));
  await act(async () => http.pending[1].resolve(Response.json([{ ...ROWS[0], id: 'accepted-post', content: 'Latest accepted post' }, ...[...http.rows].reverse()])));
  expect(screen.getByText('Latest accepted post')).toBeTruthy();
  for (const row of http.rows) expect(screen.getByText(row.content)).toBeTruthy();
  assertRequests(http.requests, 'initial', 2); expect(post).toHaveBeenCalledTimes(1);
});

it('CH.2q protection successful DELETE survives an older POST-triggered history snapshot', async () => {
  const http = await openProjectErrors('delete', 'en'), refresh = pendingResponse();
  const read = fetch, post = vi.fn(() => Promise.resolve(Response.json({ status: 'ok' }))), refreshRead = vi.fn(() => refresh.promise);
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => {
    if (init?.method === 'POST') return post();
    if (input.includes('/messages?limit=50') && !init?.method) return refreshRead();
    return read(input, init);
  }));
  changeDraft(); await act(async () => fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' }));
  await waitFor(() => expect(refreshRead).toHaveBeenCalledTimes(1));
  fireEvent.click(within(ownedRow()).getByTitle('Delete'));
  await waitFor(() => expect(http.pending).toHaveLength(1));
  await act(async () => http.pending[0].resolve(Response.json({ status: 'ok', message_id: ROWS[0].id })));
  expect(screen.getByText('[deleted]')).toBeTruthy();
  await act(async () => refresh.resolve(Response.json([...http.rows].reverse())));
  expect(screen.getByText('[deleted]')).toBeTruthy(); expect(screen.queryByText(ROWS[0].content)).toBeNull();
  expect(screen.getByText(ROWS[1].content)).toBeTruthy(); assertRequests(http.requests, 'delete', 1);
  expect(post).toHaveBeenCalledTimes(1); expect(refreshRead).toHaveBeenCalledTimes(1);
});

it.each(['post', 'patch'] as const)('CH.2q protection late %s failure cannot attach to replacement composer context', async operation => {
  const http = await openProjectErrors(operation, 'en');
  await startOperation(operation, 'en');
  if (operation === 'post') fireEvent.click(within(ownedRow()).getByTitle('Reply'));
  else fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' });
  const input = changeDraft(); fireEvent.change(input, { target: { value: 'Replacement context draft' } });
  await act(async () => http.pending[0].reject(new TypeError(SENTINEL)));
  expect(input.value).toBe('Replacement context draft');
  expect(screen.queryByText(communicationMessages.en[ERROR_KEYS[operation]])).toBeNull();
  expect(screen.queryByText(new RegExp(SENTINEL))).toBeNull();
  if (operation === 'post') expect(screen.getByText(`Replying to ${PROFILE.name}`)).toBeTruthy();
  else expect(screen.queryByText(publicMessages.en.editingMessage)).toBeNull();
  assertRequests(http.requests, operation, 1);
});

it('CH.2q protection older scroll is singleflight and failed scroll waits for explicit Retry', async () => {
  const http = await openProjectErrors('older', 'en');
  const panel = ownedRow().closest('.chat-panel'); if (!(panel instanceof HTMLElement)) throw new Error('History panel missing');
  changeDraft(); act(() => { fireEvent.scroll(panel, { target: { scrollTop: 0 } }); fireEvent.scroll(panel, { target: { scrollTop: 0 } }); });
  expect(http.pending).toHaveLength(1); assertRequests(http.requests, 'older', 1);
  await failOperation(http, 'http');
  const error = screen.getByText(communicationMessages.en['Could not load older messages.']);
  act(() => { fireEvent.scroll(panel, { target: { scrollTop: 0 } }); fireEvent.scroll(panel, { target: { scrollTop: 0 } }); });
  expect(http.pending).toHaveLength(1); assertRequests(http.requests, 'older', 1);
  assertRetained(http, 'older', 'en');
  const alert = error.closest('[role="alert"]'); if (!(alert instanceof HTMLElement)) throw new Error('Older alert missing');
  fireEvent.click(within(alert).getByRole('button', { name: sharedMessages.en.retry }));
  await waitFor(() => expect(http.pending).toHaveLength(2)); assertRequests(http.requests, 'older', 2);
  await completeRetry(http, 'older'); assertRecovered(http, 'older');
});

it('CH.2q protection refresh-token-only replacement restarts pending initial history', async () => {
  localStorage.setItem('refresh_token', 'synthetic-refresh-one');
  const http = await openProjectErrors('initial', 'en');
  const before = [localStorage.getItem('auth'), localStorage.getItem('access_token')];
  await act(async () => { localStorage.setItem('refresh_token', 'synthetic-refresh-two'); window.dispatchEvent(new Event('storage')); });
  expect([localStorage.getItem('auth'), localStorage.getItem('access_token')]).toEqual(before);
  await waitFor(() => expect(http.pending).toHaveLength(2));
  await act(async () => http.pending[0].resolve(Response.json([{ ...ROWS[0], content: 'Obsolete refresh generation' }])));
  expect(screen.queryByText('Obsolete refresh generation')).toBeNull();
  await act(async () => http.pending[1].resolve(Response.json([{ ...ROWS[0], content: 'Current refresh generation' }])));
  expect(screen.getByText('Current refresh generation')).toBeTruthy();
  assertRequests(http.requests, 'initial', 2);
});

it('CH.2q protection refresh-token-only replacement releases obsolete pending DELETE', async () => {
  localStorage.setItem('refresh_token', 'synthetic-refresh-one');
  const http = await openProjectErrors('delete', 'en');
  await startOperation('delete', 'en');
  const before = [localStorage.getItem('auth'), localStorage.getItem('access_token')];
  await act(async () => { localStorage.setItem('refresh_token', 'synthetic-refresh-two'); window.dispatchEvent(new Event('storage')); });
  expect([localStorage.getItem('auth'), localStorage.getItem('access_token')]).toEqual(before);
  await screen.findByRole('textbox');
  await act(async () => http.pending[0].resolve(Response.json({ status: 'ok', message_id: ROWS[0].id })));
  const button = within(ownedRow()).getByTitle('Delete');
  expect(button.hasAttribute('disabled')).toBe(false);
  expect(screen.queryByText('[deleted]')).toBeNull();
  fireEvent.click(button);
  await waitFor(() => expect(http.pending).toHaveLength(2));
  assertRequests(http.requests, 'delete', 2);
  await completeRetry(http, 'delete'); assertRecovered(http, 'delete');
});

it.each(['post', 'patch'] as const)('CH.2q protection empty current %s draft disables failure Retry until typing', async operation => {
  const http = await openProjectErrors(operation, 'en');
  await startOperation(operation, 'en');
  const input = screen.getByRole<HTMLTextAreaElement>('textbox');
  fireEvent.change(input, { target: { value: '' } });
  await failOperation(http, 'http');
  const text = screen.getByText(communicationMessages.en[ERROR_KEYS[operation]]);
  const alert = text.closest('[role="alert"]'); if (!(alert instanceof HTMLElement)) throw new Error('Write error alert missing');
  expect(input.value).toBe('');
  expect(input.closest('form')?.querySelector('button[type="submit"]')?.hasAttribute('disabled')).toBe(true);
  expect(within(alert).getByRole('button', { name: sharedMessages.en.retry }).hasAttribute('disabled')).toBe(true);
  expect(screen.queryByText(new RegExp(SENTINEL))).toBeNull();
  expect(screen.queryByText(publicMessages.en.editingMessage) !== null).toBe(operation === 'patch');
  changeDraft();
  await act(async () => fireEvent.keyDown(input, { key: 'Enter' }));
  await waitFor(() => expect(http.pending).toHaveLength(2));
  assertRequests(http.requests, operation, 2);
  await completeRetry(http, operation); assertRecovered(http, operation);
});
