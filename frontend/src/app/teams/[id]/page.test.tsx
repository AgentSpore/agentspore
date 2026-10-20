// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import TeamPage from './page';

vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'synthetic-team' }), usePathname: () => '/teams/synthetic-team', useRouter: () => ({ refresh: vi.fn() }) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('keeps the skip target through loading and failed requests', async () => {
  const responses: ((response: Response) => void)[] = [];
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { responses.push(resolve); })));
  render(<TeamPage />);
  expect(screen.getByRole('main').id).toBe('main-content');
  expect(screen.getByRole('main').tabIndex).toBe(-1);
  expect(responses.length).toBeGreaterThan(0);
  responses.forEach(respond => respond(new Response('{}', { status: 404 })));
  await screen.findByText('Team not found');
  expect(screen.getAllByRole('main')).toHaveLength(1);
  expect(screen.getByRole('main').id).toBe('main-content');
  expect(screen.getByRole('main').tabIndex).toBe(-1);
});
