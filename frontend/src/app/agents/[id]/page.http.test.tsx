// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import AgentPage from './page';
import { API_URL, type Agent, type ActivityEvent, type AgentBadge, type BlogPost, type GitHubActivityItem, type ModelUsageStats } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';

const route = vi.hoisted(() => ({ id: 'synthetic-a' }));
vi.mock('next/navigation', () => ({ useParams: () => route, useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));
type Locale = 'en' | 'ru';
const NOW = '2026-10-20T12:00:00Z';
const PRIVATE_FAILURE = 'Synthetic internal agent read failure';
const PARTIAL = { en: 'Some profile sections could not be loaded.', ru: 'Не удалось загрузить некоторые разделы профиля.' };
const A: Agent = { id: 'synthetic-a', name: 'Synthetic Agent A', handle: 'synthetic-a', agent_type: 'external',
  model_provider: 'synthetic', model_name: 'synthetic', specialization: 'programmer', skills: [], karma: 0,
  projects_created: 0, code_commits: 0, reviews_done: 0, last_heartbeat: null, is_active: true, created_at: NOW,
  dna_risk: 5, dna_speed: 5, dna_verbosity: 5, dna_creativity: 5, bio: null, fork_count: 0, is_hosted: false };
const B: Agent = { ...A, id: 'synthetic-b', name: 'Synthetic Agent B', handle: 'synthetic-b' };
const MODEL: ModelUsageStats = { total_calls: 0, unique_models: 0, by_model: [], by_task: [] };
function activity(id: string): ActivityEvent[] { return [{ id: `activity-${id}`, action_type: 'code_commit', description: `Activity ${id}`, ts: NOW }]; }
function github(id: string): { activities: GitHubActivityItem[] } { return { activities: [{ id: `github-${id}`, action_type: 'code_commit', description: `Commit ${id}`, created_at: NOW }] }; }
function badges(id: string): AgentBadge[] { return [{ badge_id: `badge-${id}`, name: `Badge ${id}`, description: 'Synthetic badge', icon: 'S', category: 'code', rarity: 'common', awarded_at: NOW }]; }
function posts(id: string) {
  const post: BlogPost = { id: `post-${id}`, agent_id: id, agent_name: id, agent_handle: id, title: `Post ${id}`,
    content: `Body ${id}`, reactions: { like: 0, fire: 0, insightful: 0, funny: 0 }, created_at: NOW };
  return { posts: [post], total: 1, limit: 10, offset: 0 };
}
function endpoints(id: string) { return {
  profile: `/api/v1/agents/${id}`, activity: `/api/v1/activity?agent_id=${id}&limit=50`,
  model: `/api/v1/agents/${id}/model-usage`, github: `/api/v1/agents/${id}/github-activity?limit=50`,
  badges: `/api/v1/agents/${id}/badges`, blog: `/api/v1/blog/agents/${id}/posts?limit=10&offset=0`,
}; }
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function agentHTTP() {
  const plans = new Map<string, Promise<Response>[]>(), bodies = new Map<string, unknown>();
  const calls: string[] = [], unexpected: string[] = [];
  for (const agent of [A, B]) {
    const paths = endpoints(agent.id);
    const payloads: [string, unknown][] = [[paths.profile, agent], [paths.activity, activity(agent.id)], [paths.model, MODEL],
      [paths.github, github(agent.id)], [paths.badges, badges(agent.id)], [paths.blog, posts(agent.id)]];
    for (const [path, body] of payloads) bodies.set(path, body);
  }
  const enqueue = (path: string, response: Promise<Response>) => plans.set(path, [...(plans.get(path) ?? []), response]);
  const hold = (path: string) => { const pending = deferred<Response>(); enqueue(path, pending.promise); return pending; };
  const holdBody = (path: string) => {
    const body = deferred<unknown>(), response = Response.json(null);
    response.json = () => body.promise; enqueue(path, Promise.resolve(response)); return body;
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL), path = url.pathname + url.search;
    if (!bodies.has(path) || (init?.method ?? 'GET') !== 'GET' || new Headers(init?.headers).has('Authorization')) {
      unexpected.push(path); throw new Error('Unexpected synthetic request');
    }
    calls.push(path); return plans.get(path)?.shift() ?? Response.json(bodies.get(path));
  }));
  return { calls, unexpected, hold, holdBody, bodies };
}
function openAgent(locale: Locale = 'en') {
  const tree = () => <LocaleProvider initialLocale={locale}><AgentPage /></LocaleProvider>;
  const view = render(tree());
  return { ...view, navigate: async (id: string) => { route.id = id; view.rerender(tree()); await act(async () => {}); } };
}
async function finish<T>(pending: ReturnType<typeof deferred<T>>, value: T) { await act(async () => pending.resolve(value)); }
const settle = async () => { await act(async () => {}); };
beforeEach(() => { route.id = A.id; vi.useFakeTimers(); vi.setSystemTime(new Date(NOW)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('AD.READ held primary becomes genuine 404 without generic failure or profile', async () => {
  const http = agentHTTP(), primary = http.hold(endpoints(A.id).profile); openAgent();
  expect(screen.getByRole('main').id).toBe('main-content'); expect(screen.queryByRole('heading', { name: A.name })).toBeNull();
  await finish(primary, Response.json({ detail: PRIVATE_FAILURE }, { status: 404 }));
  expect(screen.getByText(publicMessages.en.agentNotFound)).toBeTruthy();
  expect(screen.getByRole('link', { name: /Back to dashboard/ })).toBeTruthy();
  expect(screen.queryByRole('button', { name: sharedMessages.en.retry })).toBeNull(); expect(http.unexpected).toEqual([]);
});

it.each([{ locale: 'en', mode: 'http' }, { locale: 'ru', mode: 'network' }, { locale: 'en', mode: 'malformed' }, { locale: 'en', mode: 'skills-string' }, { locale: 'ru', mode: 'skills-item' }] as const)(
  'AD.READ primary $mode in $locale gives generic Retry instead of false not-found', async ({ locale, mode }) => {
    const http = agentHTTP(), primary = http.hold(endpoints(A.id).profile); openAgent(locale);
    await act(async () => {
      if (mode === 'network') primary.reject(new TypeError(PRIVATE_FAILURE));
      else {
        const invalid = mode === 'malformed' ? { ...A, id: 123 } : mode === 'skills-string' ? { ...A, skills: 'synthetic-invalid-array' } : { ...A, skills: [{}] };
        primary.resolve(Response.json(mode === 'http' ? { detail: PRIVATE_FAILURE } : invalid, { status: mode === 'http' ? 503 : 200 }));
      }
    });
    expect(screen.getByRole('alert').textContent).toContain(sharedMessages[locale].couldNotLoadData);
    expect(screen.queryByText(publicMessages[locale].agentNotFound)).toBeNull(); expect(screen.queryByText(PRIVATE_FAILURE)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: sharedMessages[locale].retry })); await settle();
    expect(screen.getByRole('heading', { name: A.name })).toBeTruthy(); expect(http.unexpected).toEqual([]);
  });

it.each([{ locale: 'en', mode: 'http', sections: ['activity', 'badges'] }, { locale: 'ru', mode: 'network', sections: ['model', 'github'] }] as const)(
  'AD.READ auxiliary $mode in $locale keeps primary and successful sections usable with explicit failures', async ({ locale, mode, sections }) => {
    const http = agentHTTP(), paths = endpoints(A.id), held = sections.map(section => http.hold(paths[section])); openAgent(locale);
    await act(async () => held.forEach(pending => mode === 'http' ? pending.resolve(Response.json({ detail: PRIVATE_FAILURE }, { status: 503 })) : pending.reject(new TypeError(PRIVATE_FAILURE))));
    expect(screen.getByRole('heading', { name: A.name })).toBeTruthy();
    const notice = screen.getByText(PARTIAL[locale]).closest('[role="alert"]');
    if (!(notice instanceof HTMLElement)) throw new Error('Missing partial profile alert');
    const label = { activity: 'activityTimeline', model: 'modelUsage', github: 'gitHubActivity', badges: 'badges' } as const;
    for (const section of sections) expect(within(notice).getByText(publicMessages[locale][label[section]])).toBeTruthy();
    expect(within(notice).getAllByRole('button', { name: sharedMessages[locale].retry })).toHaveLength(1);
    if (sections.some(section => section === 'activity')) expect(screen.queryByText(publicMessages[locale].noActivityRecordedYet)).toBeNull();
    else expect(screen.getByText(`Activity ${A.id}`)).toBeTruthy();
    expect(screen.queryByText(PRIVATE_FAILURE)).toBeNull(); http.bodies.set(paths.profile, { ...A, name: 'Updated Agent A', karma: 42 });
    fireEvent.click(within(notice).getByRole('button', { name: sharedMessages[locale].retry })); await settle();
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByRole('heading', { name: 'Updated Agent A' })).toBeTruthy(); expect(screen.getByText('42')).toBeTruthy();
    expect(screen.queryByText(PARTIAL[locale])).toBeNull(); expect(screen.getByText(`Activity ${A.id}`)).toBeTruthy(); expect(http.unexpected).toEqual([]);
  });

it.each(['profile', 'activity'] as const)('AD.READ old %s response body cannot overwrite current agent after navigation', async section => {
  const http = agentHTTP(), body = http.holdBody(endpoints(A.id)[section]), view = openAgent(); await settle();
  await view.navigate(B.id); expect(screen.getByRole('heading', { name: B.name })).toBeTruthy();
  await finish(body, section === 'profile' ? A : activity(A.id));
  expect(screen.getByRole('heading', { name: B.name })).toBeTruthy(); expect(screen.queryByText(`Activity ${A.id}`)).toBeNull();
  expect(screen.getByText(`Activity ${B.id}`)).toBeTruthy(); expect(http.unexpected).toEqual([]);
});

it('AD.READ late old primary failure cannot replace a ready new agent', async () => {
  const http = agentHTTP(), old = http.hold(endpoints(A.id).profile), view = openAgent(); await settle(); await view.navigate(B.id);
  await act(async () => old.reject(new TypeError(PRIVATE_FAILURE)));
  expect(screen.getByRole('heading', { name: B.name })).toBeTruthy(); expect(screen.queryByRole('alert')).toBeNull(); expect(http.unexpected).toEqual([]);
});

it('AD.READ old initial blog response cannot replace the new agent posts', async () => {
  const http = agentHTTP(), old = http.hold(endpoints(A.id).blog), view = openAgent(); await settle();
  expect(screen.getByRole('heading', { name: A.name })).toBeTruthy(); await view.navigate(B.id);
  expect(screen.getByText(`Body ${B.id}`)).toBeTruthy(); await finish(old, Response.json(posts(A.id)));
  expect(screen.getByText(`Body ${B.id}`)).toBeTruthy(); expect(screen.queryByText(`Body ${A.id}`)).toBeNull(); expect(http.unexpected).toEqual([]);
});

it('AD.READ unmounted primary completion cannot start an initial blog request', async () => {
  const http = agentHTTP(), old = http.hold(endpoints(A.id).profile), view = openAgent(); await settle();
  view.unmount(); const before = http.calls.length; await finish(old, Response.json(A));
  expect(http.calls).toHaveLength(before); expect(screen.queryByRole('main')).toBeNull(); expect(http.unexpected).toEqual([]);
});

it('AD.READ same-agent partial Retry retains the current blog page instead of reloading offset zero', async () => {
  const http = agentHTTP(), paths = endpoints(A.id), section = http.hold(paths.activity);
  const first = posts(A.id), post = first.posts[0];
  const nextPath = paths.blog.replace('offset=0', 'offset=10');
  http.bodies.set(paths.blog, { ...first, posts: Array.from({ length: 10 }, (_, index) => ({ ...post, id: `first-${index}`, title: `First ${index}` })), total: 11 });
  http.bodies.set(nextPath, { ...first, posts: [{ ...post, id: 'page-two', content: 'Page two retained' }], total: 11, offset: 10 });
  openAgent(); await finish(section, Response.json({}, { status: 503 })); await settle();
  fireEvent.click(screen.getByRole('button', { name: new RegExp(publicMessages.en.next) })); await settle();
  expect(screen.getByText('Page two retained')).toBeTruthy(); expect(http.calls).toContain(nextPath);
  const initialReads = http.calls.filter(path => path === paths.blog).length;
  fireEvent.click(screen.getByRole('button', { name: sharedMessages.en.retry })); await settle();
  expect(screen.getByRole('heading', { name: A.name })).toBeTruthy(); expect(screen.getByText('Page two retained')).toBeTruthy();
  expect(http.calls.filter(path => path === paths.blog)).toHaveLength(initialReads); expect(http.unexpected).toEqual([]);
});
