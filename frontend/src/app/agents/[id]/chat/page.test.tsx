// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AgentChatPage from './page';

vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'synthetic-agent' }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('keeps the skip target through loading and a missing agent', async () => {
  let respond: ((response: Response) => void) | undefined;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { respond = resolve; })));
  render(<AgentChatPage />);
  expect(screen.getByRole('main').id).toBe('main-content');
  expect(screen.getByRole('main').tabIndex).toBe(-1);
  if (!respond) throw new Error('Expected the route to request its agent');
  respond(new Response('{}', { status: 404 }));
  await screen.findByText('Agent not found');
  expect(screen.getAllByRole('main')).toHaveLength(1);
  expect(screen.getByRole('main').id).toBe('main-content');
  expect(screen.getByRole('main').tabIndex).toBe(-1);
});
