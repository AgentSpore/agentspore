// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import AgentPage from './page';

vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'example' }), useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('keeps a large stat number on one line with a size that fits two columns at 320 px', async () => {
  const agent = { id: 'example', name: 'Example Agent', handle: 'example', specialization: 'programmer', bio: null,
    skills: [], model_provider: 'p', model_name: 'm', last_heartbeat: null, created_at: '2026-10-01T00:00:00Z',
    karma: 987654321, projects_created: 0, code_commits: 0, fork_count: 0, dna_risk: 5, dna_speed: 5, dna_verbosity: 5, dna_creativity: 5 };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
    url.endsWith('/agents/example') ? agent : url.includes('/blog/') ? { posts: [], total: 0 } : url.includes('/github-activity') ? { activities: [] } : []), { status: 200 })));
  render(<LocaleProvider initialLocale="en"><AgentPage /></LocaleProvider>);
  await screen.findByRole('heading', { name: 'Example Agent' });
  const value = await screen.findByText('987,654,321', {}, { timeout: 3000 });
  expect(value.className).toContain('whitespace-nowrap');
  expect(value.className).toContain('tabular-nums');
  expect(value.className).toMatch(/(^|\s)text-base(\s|$)/);
  expect(value.closest('.stat-card')?.className).not.toContain('overflow-wrap:anywhere');
});
