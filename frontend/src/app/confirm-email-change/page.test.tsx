// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { emailChangeMessages as m } from '@/lib/i18n/emailChange';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { ConfirmEmailChange } from './ConfirmEmailChange';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
const LINK = 'a'.repeat(43);
const reply = (status: number, message = '', extra: object = {}) => new Response(JSON.stringify({ message, ...extra }), { status });

function open(hash: string, signedIn = true) {
  window.history.replaceState(null, '', `/confirm-email-change${hash}`);
  if (signedIn) { localStorage.setItem('access_token', 'synthetic-access'); localStorage.setItem('refresh_token', 'synthetic-refresh'); }
  render(<LocaleProvider initialLocale="en"><ConfirmEmailChange /></LocaleProvider>);
}
function fakeStorage() {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } };
}
beforeEach(() => { vi.stubGlobal('localStorage', fakeStorage()); vi.stubGlobal('sessionStorage', fakeStorage()); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('confirms the new address and removes the token from the address bar', async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => reply(200, 'Email changed.'));
  vi.stubGlobal('fetch', fetchMock);
  open(`#token=${LINK}`);
  expect(await screen.findByText(m.en.done)).toBeTruthy();
  expect(window.location.hash).toBe('');
  expect(fetchMock).toHaveBeenCalledOnce();
  expect(fetchMock.mock.calls[0]).toMatchObject([expect.stringContaining('/users/me/email/change/confirm'),
    { method: 'POST', body: JSON.stringify({ token: LINK }) }]);
  expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get('Authorization')).toBe('Bearer synthetic-access');
  expect(screen.getByRole('link', { name: m.en.toProfile }).getAttribute('href')).toBe('/profile');
});

it('tells the owner to check the new mailbox after step one of two', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => reply(200, 'Approved. If the new address can be used, a confirmation link was sent to it.')));
  open(`#token=${LINK}`);
  expect(await screen.findByText(m.en.approved)).toBeTruthy();
});

it.each([[400, 'invalid'], [409, 'pending']] as const)('shows a plain reason for %s', async (status, key) => {
  vi.stubGlobal('fetch', vi.fn(async () => reply(status, 'internal detail')));
  open(`#token=${LINK}`);
  expect((await screen.findByRole('alert')).textContent).toBe(m.en[key]);
  expect(screen.queryByText('internal detail')).toBeNull();
});

it('offers a retry after a network error and keeps the link usable', async () => {
  const fetchMock = vi.fn()
    .mockRejectedValueOnce(new TypeError('offline'))
    .mockResolvedValueOnce(reply(200, 'Email changed.'));
  vi.stubGlobal('fetch', fetchMock);
  open(`#token=${LINK}`);
  fireEvent.click(await screen.findByRole('button', { name: m.en.retry }));
  expect(await screen.findByText(m.en.done)).toBeTruthy();
  expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({ token: LINK });
});

it('keeps the token through sign-in when signed out', async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  open(`#token=${LINK}`, false);
  const signIn = await screen.findByRole('link', { name: m.en.signInLink });
  expect(signIn.getAttribute('href')).toBe('/login?next=/confirm-email-change');
  expect(signIn.getAttribute('href')).not.toContain(LINK);
  expect(window.location.hash).toBe('');
  expect(JSON.parse(sessionStorage.getItem('email_change_link') ?? 'null').token).toBe(LINK);
  expect(fetchMock).not.toHaveBeenCalled();
});

it('resumes the stashed link after returning signed in, then forgets it', async () => {
  sessionStorage.setItem('email_change_link', JSON.stringify({ token: LINK, until: Date.now() + 60000 }));
  vi.stubGlobal('fetch', vi.fn(async () => reply(200, 'Email changed.')));
  open('');
  expect(await screen.findByText(m.en.done)).toBeTruthy();
  expect(sessionStorage.getItem('email_change_link')).toBeNull();
});

it('explains a page opened without a link', async () => {
  open('');
  expect((await screen.findByRole('alert')).textContent).toBe(m.en.missing);
});

it.each([[422, 'invalid'], [503, 'down']] as const)('never calls the link still valid on %s', async (status, key) => {
  vi.stubGlobal('fetch', vi.fn(async () => reply(status)));
  open(`#token=${LINK}`);
  const text = (await screen.findByRole('alert')).textContent;
  expect(text).toBe(m.en[key]);
  expect(text).not.toMatch(/still valid/i);
  expect(sessionStorage.getItem('email_change_link') === null).toBe(key === 'invalid');
});

it('refreshes an expired access token silently instead of asking to sign in', async () => {
  const fetchMock = vi.fn<typeof fetch>(async (url, init) => {
    if (String(url).includes('/auth/refresh')) return new Response(JSON.stringify({ access_token: 'fresh-access', refresh_token: 'fresh-refresh' }), { status: 200 });
    return new Headers(init?.headers).get('Authorization') === 'Bearer fresh-access' ? reply(200, 'Email changed.') : reply(401);
  });
  vi.stubGlobal('fetch', fetchMock);
  open(`#token=${LINK}`);
  expect(await screen.findByText(m.en.done)).toBeTruthy();
});

it.each([
  ['approve_old', 'Anything reworded', 'approved'], ['done', 'Approved. reworded', 'done'], ['confirm_new', 'x', 'done'],
] as const)('reads the machine step %s over the wording', async (step, message, key) => {
  vi.stubGlobal('fetch', vi.fn(async () => reply(200, message, { step })));
  open(`#token=${LINK}`);
  expect(await screen.findByText(m.en[key])).toBeTruthy();
});

it('drops a stashed link older than ten minutes', async () => {
  sessionStorage.setItem('email_change_link', JSON.stringify({ token: LINK, until: Date.now() - 1 }));
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  open('');
  expect((await screen.findByRole('alert')).textContent).toBe(m.en.missing);
  expect(fetchMock).not.toHaveBeenCalled();
  expect(sessionStorage.getItem('email_change_link')).toBeNull();
});

it('stashes the link with a ten minute expiry', () => {
  open(`#token=${LINK}`, false);
  const stored = JSON.parse(sessionStorage.getItem('email_change_link') ?? 'null');
  expect(stored.token).toBe(LINK);
  expect(stored.until - Date.now()).toBeGreaterThan(590000);
  expect(stored.until - Date.now()).toBeLessThanOrEqual(600000);
});
