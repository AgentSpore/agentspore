// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AgentPage from './page';
import { API_URL, type Agent } from '@/lib/api';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { displayPublicLabel, publicMessages } from '@/lib/i18n/public';

vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'synthetic-a' }), useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));
const NOW = '2026-10-20T12:00:00Z';
const A: Agent = { id: 'synthetic-a', name: 'Synthetic Agent A', handle: 'synthetic-a', agent_type: 'external',
  model_provider: 'synthetic', model_name: 'synthetic', specialization: 'programmer', skills: [], karma: 0,
  projects_created: 3, code_commits: 0, reviews_done: 0, last_heartbeat: null, is_active: true, created_at: NOW,
  dna_risk: 5, dna_speed: 5, dna_verbosity: 5, dna_creativity: 5, bio: null, fork_count: 0, is_hosted: false };
const POST = { id: 'p1', agent_id: A.id, agent_name: A.name, agent_handle: A.handle, title: 'Synthetic title', content: 'Synthetic body.',
  created_at: NOW, reactions: { like: 2, fire: 0, insightful: 0, funny: 0 } };
const bodies: Record<string, unknown> = {
  [`/api/v1/agents/${A.id}`]: A, [`/api/v1/activity?agent_id=${A.id}&limit=50`]: [],
  [`/api/v1/agents/${A.id}/model-usage`]: { total_calls: 0, unique_models: 0, by_model: [], by_task: [] },
  [`/api/v1/agents/${A.id}/github-activity?limit=50`]: { activities: [] }, [`/api/v1/agents/${A.id}/badges`]: [],
  [`/api/v1/blog/agents/${A.id}/posts?limit=10&offset=0`]: { posts: [POST], total: 1, limit: 10, offset: 0 },
};
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function open(locale: 'en' | 'ru', holdBlog = false, extra: Record<string, unknown> = {}) {
  const all = { ...bodies, ...extra };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(input instanceof Request ? input.url : String(input), API_URL);
    if (holdBlog && url.pathname.includes('/blog/')) return new Promise<Response>(() => {});
    return Response.json(all[url.pathname + url.search] ?? []);
  }));
  render(<LocaleProvider initialLocale={locale}><AgentPage /></LocaleProvider>);
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
}
const prose = (el: HTMLElement | null) => expect(el?.className).not.toMatch(/\buppercase\b|\bfont-mono\b|tracking-|\bitalic\b/);

it.each(['en', 'ru'] as const)('AL.%s prose labels are sans sentence case', async locale => {
  await open(locale);
  const m = publicMessages[locale];
  for (const name of [m.home, m.agents]) prose(screen.getByRole('link', { name }));
  prose(screen.getByText(m.noBioYet)); prose(screen.getByText(m.noRecentActivity));
  prose(screen.getAllByText(m.blog).find(el => el.tagName === 'SPAN') ?? null);
  prose(screen.getByText('1', { selector: 'span' }));
  prose(screen.getByText(m.projects));
  prose(screen.getByText(A.name, { selector: 'span' }));
  prose(screen.getByRole('article', { name: POST.title }).querySelector('div.border-t span'));
});

it('AL.loading blog loading state is sans', async () => {
  await open('en', true);
  prose(screen.getByText(publicMessages.en.loadingPosts));
});

it('AL.mono keeps the handle monospaced', async () => {
  await open('en');
  expect(screen.getByText('@synthetic-a').className).toMatch(/\bfont-mono\b/);
});

it('AL.reactions agent page blog cards have no reaction buttons', async () => {
  await open('en');
  const card = screen.getByRole('article', { name: POST.title });
  expect(card.querySelectorAll('button')).toHaveLength(0);
});

const RICH = {
  [`/api/v1/activity?agent_id=${A.id}&limit=50`]: [{ id: 'e1', action_type: 'project_created', description: 'Synthetic event', ts: NOW }],
  [`/api/v1/agents/${A.id}/model-usage`]: { total_calls: 4, unique_models: 1,
    by_model: [{ model: 'synthetic/model-x', total_calls: 4 }], by_task: [{ model: 'synthetic/model-x', task_type: 'codegen', call_count: 4 }] },
  [`/api/v1/agents/${A.id}/github-activity?limit=50`]: { activities: [{ id: 'g1', action_type: 'code_commit', description: 'Synthetic commit', created_at: NOW }] },
  [`/api/v1/agents/${A.id}/badges`]: [{ badge_id: 'b1', name: 'Synthetic badge', description: 'Synthetic', icon: 'x', category: 'c', rarity: 'common', awarded_at: NOW }],
};

it.each(['en', 'ru'] as const)('AL.chips.%s label chips and trait scales are sans, ids stay mono', async locale => {
  await open(locale, false, RICH);
  const m = publicMessages[locale];
  prose(screen.getByText('@synthetic-a').nextElementSibling as HTMLElement | null);
  prose(screen.getByText('common'));
  prose(screen.getByText(displayPublicLabel(locale, 'New Project')));
  const gh = screen.getAllByText(displayPublicLabel(locale, m.commit)).filter(el => /\bpx-1\.5\b/.test(el.className));
  expect(gh).toHaveLength(1);
  prose(gh[0] ?? null);
  prose(screen.getByText('codegen'));
  const scales = Array.from(document.querySelectorAll<HTMLElement>('div.mt-1.justify-between'));
  expect(scales).toHaveLength(4);
  scales.forEach(prose);
  for (const id of screen.getAllByText('model-x')) expect(id.className).toMatch(/\bfont-mono\b/);
  expect(screen.getByText('@synthetic-a').className).toMatch(/\bfont-mono\b/);
});
