// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import AgentPage from './page';

vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'example' }), useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function setup(locale: 'en' | 'ru', activityError = false) {
  const agent = { id: 'example', name: 'Example Agent', handle: 'example', specialization: 'programmer',
    bio: null, skills: ['python'], model_provider: 'provider', model_name: 'model', last_heartbeat: null,
    created_at: '2026-10-01T00:00:00Z', karma: 0, projects_created: 0, code_commits: 0, fork_count: 0,
    dna_risk: 5, dna_speed: 5, dna_verbosity: 5, dna_creativity: 5 };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/activity?') && activityError) return new Response('', { status: 500 });
    const value = url.endsWith('/agents/example') ? agent
      : url.includes('/activity?') ? [{ id: 'h', action_type: 'heartbeat', description: 'Heartbeat: idle, 0 tasks, 0 notifications, 0 DMs', ts: '2026-10-01T00:00:00Z' }, { id: 'r', action_type: 'registered', description: 'Agent joined AgentSpore', ts: '2026-10-01T00:00:00Z' }]
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
