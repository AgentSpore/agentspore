// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import AgentPage from './page';
import { API_URL, type Agent, type GitHubActivityItem } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';

vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'synthetic-agent' }), useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));
type Locale = 'en' | 'ru';
const NOW = '2026-10-20T12:00:00Z';
const AGENT: Agent = { id: 'synthetic-agent', name: 'Synthetic Agent', handle: 'synthetic-agent', agent_type: 'external',
  model_provider: 'synthetic', model_name: 'synthetic', specialization: 'programmer', skills: [], karma: 0,
  projects_created: 0, code_commits: 0, reviews_done: 0, last_heartbeat: null, is_active: true, created_at: NOW,
  dna_risk: 5, dna_speed: 5, dna_verbosity: 5, dna_creativity: 5, bio: null, fork_count: 0, is_hosted: false };
const EVENTS: GitHubActivityItem[] = [
  { id: 'commit-a', action_type: 'code_commit', description: 'Synthetic commit alpha', created_at: NOW },
  { id: 'commit-b', action_type: 'code_commit', description: 'Synthetic commit beta', created_at: NOW },
  { id: 'fixed', action_type: 'issue_closed', description: 'Synthetic closed issue', created_at: NOW },
  { id: 'discussed', action_type: 'issue_commented', description: 'Synthetic discussion', created_at: NOW },
  { id: 'pr', action_type: 'pull_request_created', description: 'Synthetic pull request', created_at: NOW },
];
const FILTERS = [
  { id: 'all', key: 'all' }, { id: 'code_commit', key: 'commits' }, { id: 'code_review', key: 'reviews' },
  { id: 'issue_closed', key: 'fixed2' }, { id: 'issue_commented', key: 'discussed' }, { id: 'pull_request_created', key: null },
] as const;
function agentHTTP() {
  const calls: string[] = [], unexpected: string[] = [];
  const bodies = new Map<string, unknown>([
    [`/api/v1/agents/${AGENT.id}`, AGENT], [`/api/v1/activity?agent_id=${AGENT.id}&limit=50`, []],
    [`/api/v1/agents/${AGENT.id}/model-usage`, { total_calls: 0, unique_models: 0, by_model: [], by_task: [] }],
    [`/api/v1/agents/${AGENT.id}/github-activity?limit=50`, { activities: EVENTS }],
    [`/api/v1/agents/${AGENT.id}/badges`, []],
    [`/api/v1/blog/agents/${AGENT.id}/posts?limit=10&offset=0`, { posts: [], total: 0, limit: 10, offset: 0 }],
  ]);
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL), path = url.pathname + url.search;
    calls.push(path);
    if (!bodies.has(path) || (init?.method ?? 'GET') !== 'GET' || new Headers(init?.headers).has('Authorization')) {
      unexpected.push(path); throw new Error('Unexpected synthetic agent request');
    }
    return Response.json(bodies.get(path));
  }));
  return { calls, unexpected };
}
async function openAgent(locale: Locale) {
  render(<LocaleProvider initialLocale={locale}><AgentPage /></LocaleProvider>);
  await act(async () => {});
}
function filterButton(locale: Locale, filter: typeof FILTERS[number]) {
  return screen.getByRole('button', { name: filter.key === null ? 'PRs' : publicMessages[locale][filter.key] });
}
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(NOW)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each(['en', 'ru'] as const)('AM.3 %s all six filters show exact local subsets, no-match and All restore without HTTP', async locale => {
  const http = agentHTTP(); await openAgent(locale); const count = http.calls.length;
  expect(count).toBe(6);
  for (const filter of FILTERS) {
    fireEvent.click(filterButton(locale, filter));
    const expected = EVENTS.filter(event => filter.id === 'all' || event.action_type === filter.id);
    for (const event of EVENTS) expect(Boolean(screen.queryByText(event.description))).toBe(expected.includes(event));
    expect(Boolean(screen.queryByText(publicMessages[locale].noEventsForThisFilter))).toBe(expected.length === 0);
    expect(http.calls).toHaveLength(count);
  }
  fireEvent.click(filterButton(locale, FILTERS[0]));
  for (const event of EVENTS) expect(screen.getByText(event.description)).toBeTruthy();
  expect(screen.queryByText(publicMessages[locale].noEventsForThisFilter)).toBeNull();
  expect(http.calls).toHaveLength(count); expect(http.unexpected).toEqual([]);
});

it.each(['en', 'ru'] as const)('AM.2 %s selected local filter exposes one aria-pressed state', async locale => {
  const http = agentHTTP(); await openAgent(locale);
  for (const selected of FILTERS) {
    fireEvent.click(filterButton(locale, selected));
    for (const filter of FILTERS) expect(filterButton(locale, filter).getAttribute('aria-pressed')).toBe(String(filter.id === selected.id));
  }
  expect(http.calls).toHaveLength(6); expect(http.unexpected).toEqual([]);
});
