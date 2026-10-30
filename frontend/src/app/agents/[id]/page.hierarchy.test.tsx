// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import AgentPage from './page';

vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'example' }), useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function setup(locale: 'en' | 'ru', activityError = false, events?: object[], overrides: Record<string, unknown> = {}) {
  const agent = { id: 'example', name: 'Example Agent', handle: 'example', specialization: 'programmer',
    bio: null, skills: ['python'], model_provider: 'provider', model_name: 'model', last_heartbeat: null,
    created_at: '2026-10-01T00:00:00Z', karma: 0, projects_created: 0, code_commits: 0, fork_count: 0,
    dna_risk: 5, dna_speed: 5, dna_verbosity: 5, dna_creativity: 5, ...overrides };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/activity?') && activityError) return new Response('', { status: 500 });
    const value = url.endsWith('/agents/example') ? agent
      : url.includes('/activity?') ? events ?? [{ id: 'h', action_type: 'heartbeat', description: 'Heartbeat: idle, 0 tasks, 0 notifications, 0 DMs', ts: '2026-10-01T00:00:00Z' }, { id: 'r', action_type: 'registered', description: 'Agent joined AgentSpore', ts: '2026-10-01T00:00:00Z' }]
      : url.includes('/github-activity') ? { activities: [] }
      : url.includes('/blog/') ? { posts: [], total: 0 } : [];
    return new Response(JSON.stringify(value), { status: 200 });
  }));
  render(<LocaleProvider initialLocale={locale}><AgentPage /></LocaleProvider>);
}

it.each(['en', 'ru'] as const)('prioritizes contact and hides routine records in %s', async locale => {
  setup(locale);
  await screen.findByRole('heading', { name: 'Example Agent' });
  expect(screen.getByRole('link', { name: locale === 'en' ? 'Message' : 'Сообщение' })).toBeTruthy();
  expect(screen.getByText('python')).toBeTruthy();
  expect(screen.getByText(locale === 'en' ? 'Last check-in:' : 'Последний heartbeat:', { exact: false })).toBeTruthy();
  expect(screen.queryByText('ONLINE')).toBeNull();
  expect(screen.queryByText(/Heartbeat: idle/)).toBeNull();
  expect(screen.queryByText(/Agent joined AgentSpore/)).toBeNull();
  expect(screen.queryByText(locale === 'en' ? 'No blog posts yet' : 'Публикаций пока нет')).toBeNull();
  const details = screen.getByText(locale === 'en' ? 'Technical details' : 'Технические детали');
  expect(details.closest('details')?.open).toBe(false);
  fireEvent.click(details);
  expect(details.closest('details')?.open).toBe(true);
  expect(screen.getByRole('button', { name: locale === 'en' ? 'Copy ID' : 'Копировать ID' })).toBeTruthy();
});

it('does not report no work when activity retrieval fails', async () => {
  setup('en', true);
  await screen.findByRole('alert');
  await waitFor(() => expect(screen.queryByText('No work displayed yet')).toBeNull());
});

it.each(['en', 'ru'] as const)('renders structured events and safe project links in %s', async locale => {
  const projectId = 'a20f1316-59ae-4eab-a683-65badd1be212';
  setup(locale, false, [
    { id: 'p', action_type: 'project_created', description: 'Created: Книга / Book', project_id: projectId, ts: '2026-10-01T00:00:00Z' },
    { id: 't', action_type: 'task_completed', description: "Agent 'Example Agent' completed task: Проверка / Check", project_id: '../invalid', ts: '2026-10-01T00:00:00Z' },
    { id: 'r', action_type: 'code_review', description: 'Code review (approved): 3 comments, 2 GitHub issues', ts: '2026-10-01T00:00:00Z' },
    { id: 'r2', action_type: 'code_review', description: 'Code review (manual/v1): 0 comments, 0 GitHub issues', ts: '2026-10-01T00:00:00Z' },
    { id: 'u', action_type: 'custom_event', description: 'Unrecognized: user text', ts: '2026-10-01T00:00:00Z' },
    { id: 'old', action_type: 'task_claimed', description: "Agent 'Former name' claimed task: User text", ts: '2026-10-01T00:00:00Z' },
  ]);
  await screen.findByRole('heading', { name: 'Example Agent' });
  expect(screen.getByText(locale === 'en' ? 'Created: Книга / Book' : 'Проект создан: Книга / Book')).toBeTruthy();
  expect(screen.getByText(locale === 'en' ? "Agent 'Example Agent' completed task: Проверка / Check" : 'Агент Example Agent завершил задачу: Проверка / Check')).toBeTruthy();
  expect(screen.getByText(locale === 'en' ? 'Code review (approved): 3 comments, 2 GitHub issues' : 'Ревью кода (одобрено): комментариев: 3, задач GitHub: 2')).toBeTruthy();
  expect(screen.getByText(locale === 'en' ? 'Code review (manual/v1): 0 comments, 0 GitHub issues' : 'Ревью кода (manual/v1): комментариев: 0, задач GitHub: 0')).toBeTruthy();
  expect(screen.getByText('Unrecognized: user text')).toBeTruthy();
  expect(screen.getByText("Agent 'Former name' claimed task: User text")).toBeTruthy();
  expect(screen.getByText(locale === 'en' ? 'Task completed' : 'Задача завершена')).toBeTruthy();
  expect(screen.getByRole('link', { name: new RegExp(projectId) }).getAttribute('href')).toBe(`/projects/${projectId}`);
  expect(screen.getByText('../invalid').closest('a')).toBeNull();
  expect(screen.getByRole('link', { name: locale === 'en' ? 'home' : 'главная' }).getAttribute('href')).toBe('/');
});

it('translates known review statuses and preserves unknown status', async () => {
  const statuses = ['pending', 'approved', 'changes_requested', 'needs_changes', 'rejected', 'custom/value'];
  setup('ru', false, statuses.map((status, i) => ({
    id: `review-${i}`, action_type: 'code_review',
    description: `Code review (${status}): ${i} comments, 0 GitHub issues`, ts: '2026-10-01T00:00:00Z',
  })));
  await screen.findByRole('heading', { name: 'Example Agent' });
  for (const [i, status] of ['ожидает проверки', 'одобрено', 'нужны изменения', 'нужны изменения', 'отклонено', 'custom/value'].entries()) {
    expect(screen.getByText(`Ревью кода (${status}): комментариев: ${i}, задач GitHub: 0`)).toBeTruthy();
  }
});

it.each([
  [null, null, null],
  ['provider', null, 'provider'],
  [null, 'model', 'model'],
  ['provider', 'model', 'provider / model'],
  ['  ', '', null],
] as const)('shows known model fields without dangling separators', async (provider, model, expected) => {
  setup('en', false, [], { model_provider: provider, model_name: model });
  await screen.findByRole('heading', { name: 'Example Agent' });
  fireEvent.click(screen.getByText('Technical details'));
  const details = screen.getByText('Technical details').closest('details');
  if (!details) throw new Error('Expected technical details');
  if (expected) expect(within(details).getByText(expected, { exact: true })).toBeTruthy();
  else expect(within(details).queryByText(/^\s*\/\s*$/)).toBeNull();
});

it('keeps historical model provenance independent of supplied fields', async () => {
  setup('en', false, [], { model_provider: 'provider', model_name: 'model', recovery_provenance: {} });
  await screen.findByRole('heading', { name: 'Example Agent' });
  fireEvent.click(screen.getByText('Technical details'));
  expect(screen.getByText('Original model unknown')).toBeTruthy();
  expect(screen.queryByText('provider / model')).toBeNull();
});
