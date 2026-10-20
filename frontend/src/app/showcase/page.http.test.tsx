// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppRouterContext } from 'next/dist/shared/lib/app-router-context.shared-runtime';
import ShowcasePage from './page';
import { API_URL, type Project } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { sharedMessages } from '@/lib/i18n/shared';
import { SHOWCASE_COPY } from './curatedServices';

vi.mock('@/components/Header', () => ({ Header: () => null }));
type Locale = 'en' | 'ru';
const PRIVATE_FAILURE = 'Synthetic internal catalog failure';
const router = { back: vi.fn(), forward: vi.fn(), refresh: vi.fn(), push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), bfcacheId: 'synthetic-route' };
const PROJECT: Project = { id: 'synthetic-project', title: 'Synthetic catalog project', description: 'Synthetic description',
  category: 'devtools', status: 'deployed', votes_up: 0, votes_down: 0, score: 0,
  repo_url: 'https://github.com/Example/verdict', deploy_url: 'https://example.invalid/demo', tech_stack: ['TypeScript'],
  hackathon_id: null, creator_agent_id: 'synthetic-agent', agent_name: 'Synthetic agent', agent_handle: 'synthetic-agent',
  github_stars: 0, created_at: '2026-10-20T12:00:00Z' };
const hiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden');
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-20T12:00:00Z'));
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
});
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.useRealTimers();
  if (hiddenDescriptor) Object.defineProperty(document, 'hidden', hiddenDescriptor);
  else Reflect.deleteProperty(document, 'hidden');
});
function deferredResponse() {
  let resolve!: (response: Response) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<Response>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function catalogHTTP() {
  const queue: Promise<Response>[] = [], requests: string[] = [], unexpected: string[] = [];
  const hold = () => { const pending = deferredResponse(); queue.push(pending.promise); return pending; };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL), response = queue.shift();
    if (url.pathname !== '/api/v1/projects' || url.search !== '?limit=200' || (init?.method ?? 'GET') !== 'GET' || new Headers(init?.headers).has('Authorization') || !response) {
      unexpected.push(url.pathname + url.search); throw new Error('Unexpected synthetic request');
    }
    requests.push(url.pathname + url.search); return response;
  }));
  return { hold, requests, unexpected };
}
function openCatalog(locale: Locale) {
  render(<AppRouterContext.Provider value={router}><LocaleProvider initialLocale={locale}><ShowcasePage /></LocaleProvider></AppRouterContext.Provider>);
  const section = screen.getByRole('heading', { name: SHOWCASE_COPY[locale].catalog }).closest('section');
  if (!section) throw new Error('Missing project catalog section');
  return within(section);
}
async function finish(pending: ReturnType<typeof deferredResponse>, projects: Project[]) {
  await act(async () => pending.resolve(Response.json(projects)));
}

it.each(['en', 'ru'] as const)('SC.HTTP held initial catalog in %s has unknown count until confirmed empty', async locale => {
  const http = catalogHTTP(), initial = http.hold(), catalog = openCatalog(locale), copy = SHOWCASE_COPY[locale];
  expect(catalog.queryByText(`0 ${copy.apps}`)).toBeNull();
  expect(catalog.queryByText(copy.empty)).toBeNull(); expect(catalog.queryByText(copy.unavailable)).toBeNull();
  await finish(initial, []);
  expect(catalog.getByText(`0 ${copy.apps}`)).toBeTruthy(); expect(catalog.getByText(copy.empty)).toBeTruthy();
  expect(catalog.queryByText(copy.unavailable)).toBeNull(); expect(http.requests).toHaveLength(1); expect(http.unexpected).toEqual([]);
});

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }] as const)(
  'SC.HTTP first $mode error in $locale stays unavailable with unknown count and one working Retry', async ({ locale, mode }) => {
    const http = catalogHTTP(), initial = http.hold(), catalog = openCatalog(locale), copy = SHOWCASE_COPY[locale];
    await act(async () => {
      if (mode === 'http') initial.resolve(Response.json({ detail: PRIVATE_FAILURE }, { status: 503 }));
      else initial.reject(new TypeError(PRIVATE_FAILURE));
    });
    expect(catalog.getByText(copy.unavailable)).toBeTruthy(); expect(catalog.queryByText(copy.empty)).toBeNull();
    expect(catalog.queryByText(`0 ${copy.apps}`)).toBeNull(); expect(screen.queryByText(PRIVATE_FAILURE)).toBeNull();
    expect(catalog.getAllByRole('button', { name: sharedMessages[locale].retry })).toHaveLength(1);
    const next = http.hold(); fireEvent.click(catalog.getByRole('button', { name: sharedMessages[locale].retry }));
    await finish(next, [PROJECT]);
    expect(catalog.getByRole('heading', { name: PROJECT.title })).toBeTruthy(); expect(catalog.getByText(`1 ${copy.apps}`)).toBeTruthy();
    expect(catalog.queryByText(copy.unavailable)).toBeNull(); expect(http.requests).toHaveLength(2); expect(http.unexpected).toEqual([]);
  });

it.each(['en', 'ru'] as const)('SC.HTTP a successful unlisted response in %s is catalog zero, not a data failure', async locale => {
  const http = catalogHTTP(), initial = http.hold(), catalog = openCatalog(locale), copy = SHOWCASE_COPY[locale];
  await finish(initial, [{ ...PROJECT, repo_url: 'https://github.com/Example/synthetic-unlisted-project' }]);
  expect(catalog.getByText(`0 ${copy.apps}`)).toBeTruthy(); expect(catalog.getByText(copy.empty)).toBeTruthy();
  expect(catalog.queryByText(copy.unavailable)).toBeNull(); expect(catalog.queryByRole('status')).toBeNull();
  expect(catalog.queryByRole('heading', { name: PROJECT.title })).toBeNull(); expect(http.unexpected).toEqual([]);
});
