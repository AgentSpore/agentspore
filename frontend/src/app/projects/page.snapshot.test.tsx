// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProjectsPage from './page';
import { API_URL, type Project } from '@/lib/api';
import { displayPublicLabel, publicMessages } from '@/lib/i18n/public';

// Next navigation is the browser routing boundary; application components and hooks run unchanged.
vi.mock('next/navigation', () => ({ usePathname: () => '/projects' }));

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

const PROJECT: Project = {
  id: 'synthetic-project', title: 'Synthetic project', description: 'Synthetic description',
  category: 'productivity', status: 'deployed', votes_up: 2, votes_down: 1, score: 1,
  repo_url: null, deploy_url: null, tech_stack: ['TypeScript'], hackathon_id: null,
  creator_agent_id: 'synthetic-agent', agent_name: 'Synthetic agent', agent_handle: 'synthetic-agent',
  github_stars: 0, created_at: '2026-10-15T00:00:00Z',
};

it.each(['24h', '7d', '30d'] as const)('keeps the real poll snapshot at the %s boundary during later renders', async window => {
  const snapshot = Date.parse('2026-10-19T00:00:00Z');
  const duration = { '24h': 864e5, '7d': 7 * 864e5, '30d': 30 * 864e5 }[window];
  vi.setSystemTime(snapshot);
  const projects: Project[] = [
    { ...PROJECT, id: 'boundary', title: 'Exact boundary', created_at: new Date(snapshot - duration).toISOString() },
    { ...PROJECT, id: 'outside', title: 'Outside boundary', created_at: new Date(snapshot - duration - 1).toISOString() },
  ];
  const http = vi.fn(async (input: RequestInfo | URL) => {
    expect(String(input)).toBe(`${API_URL}/api/v1/projects?limit=100`);
    return new Response(JSON.stringify(projects));
  });
  vi.stubGlobal('fetch', http);
  await act(async () => { render(<ProjectsPage />); });
  expect(http).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: displayPublicLabel('en', window) }));
  expect(screen.getByRole('link', { name: 'Exact boundary' })).toBeTruthy();
  expect(screen.queryByRole('link', { name: 'Outside boundary' })).toBeNull();

  // Move wall time without running polling timers, then cause an independent page render.
  vi.setSystemTime(snapshot + 60000);
  fireEvent.change(screen.getByRole('textbox', { name: publicMessages.en.search }), { target: { value: 'Exact' } });
  expect(screen.getByRole('link', { name: 'Exact boundary' })).toBeTruthy();
  expect(screen.queryByRole('link', { name: 'Outside boundary' })).toBeNull();
  expect(http).toHaveBeenCalledTimes(1);
});
