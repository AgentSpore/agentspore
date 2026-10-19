// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { renderToString } from 'react-dom/server';
import ProjectPage from './page';

const route = vi.hoisted(() => ({ id: 'demo-project' }));
// Next navigation is the browser routing boundary; Header and auth subscriptions run unchanged.
vi.mock('next/navigation', () => ({ useParams: () => route, usePathname: () => `/projects/${route.id}` }));
beforeEach(() => { route.id = 'demo-project'; localStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function projectButton(name: RegExp) {
  const buttons = screen.getAllByRole('button', { name }).filter(button => !button.closest('header'));
  expect(buttons).toHaveLength(1);
  return buttons[0];
}

function mockProject(status: string, deployUrl: string | null) {
  const project = {
    id: "demo-project", title: "Example Project", description: "Synthetic demo fixture",
    category: "tools", status, repo_url: "https://github.com/Example/demo",
    deploy_url: deployUrl, tech_stack: [], agent_name: "Demo", agent_handle: "demo",
    creator_agent_id: "demo-agent", votes_up: 0, votes_down: 0,
    created_at: "2026-10-01T00:00:00Z",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (input.endsWith("/auth/me") && new Headers(init?.headers).get("Authorization") === "Bearer malformed") return new Response("{}", { status: 401 });
    const body = input.endsWith("/auth/me") ? { id: "synthetic-user", email: "oauth@example.invalid", name: "Synthetic account", avatar_url: null, token_balance: 0, is_admin: false }
      : input.endsWith("/ownership") ? null
      : input.endsWith("/contributors") ? { contributors: [] } : project;
    return new Response(JSON.stringify(body), { status: 200 });
  }));
}

it('reacts to legacy sign-in and sign-out storage changes without remounting', async () => {
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByRole('heading', { name: 'Example Project' });
  act(() => {
    localStorage.setItem('auth', JSON.stringify({ token: 'synthetic-session', email: 'synthetic@example.invalid', userId: 'synthetic-user' }));
    window.dispatchEvent(new Event('storage'));
  });
  await screen.findByText('synthetic@example.invalid');
  act(() => { localStorage.removeItem('auth'); window.dispatchEvent(new Event('storage')); });
  await waitFor(() => expect(screen.queryByText('synthetic@example.invalid')).toBeNull());
});

it('ignores malformed legacy storage and updates from an OAuth session', async () => {
  localStorage.setItem('auth', '{malformed');
  const payload = btoa(JSON.stringify({ sub: 'synthetic-user', email: 'synthetic@example.invalid' }));
  localStorage.setItem('access_token', `synthetic.${payload}.fixture`);
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByText('synthetic@example.invalid');
});

it('does not show the previous project while the next project is loading', async () => {
  mockProject('active', null);
  const view = render(<ProjectPage />);
  await screen.findByRole('heading', { name: 'Example Project' });
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})));
  route.id = 'next-project';
  view.rerender(<ProjectPage />);
  expect(screen.queryByRole('heading', { name: 'Example Project' })).toBeNull();
});

it('discards a chat response after leaving the chat tab', async () => {
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByRole('heading', { name: 'Example Project' });
  let resolve: (response: Response) => void = () => {};
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(done => { resolve = done; })));
  fireEvent.click(screen.getByRole('button', { name: /discussion/i }));
  fireEvent.click(screen.getByRole('button', { name: /overview/i }));
  await act(async () => { resolve(new Response(JSON.stringify([{ id: 'stale', sender_name: 'Synthetic', sender_type: 'human', sender_handle: null, content: 'Stale chat', message_type: 'text', created_at: '2026-10-19T00:00:00Z' }]))); });
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})));
  fireEvent.click(screen.getByRole('button', { name: /discussion/i }));
  expect(screen.queryByText('Stale chat')).toBeNull();
});


it('signs out of both legacy and OAuth sessions', async () => {
  localStorage.setItem('auth', JSON.stringify({ token: 'synthetic-session', email: 'synthetic@example.invalid', userId: 'synthetic-user' }));
  localStorage.setItem('access_token', `synthetic.${btoa(JSON.stringify({ sub: 'synthetic-user' }))}.fixture`);
  localStorage.setItem('refresh_token', 'synthetic-refresh');
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByText('synthetic@example.invalid');
  fireEvent.click(projectButton(/sign out/i));
  expect(localStorage.getItem('auth')).toBeNull();
  expect(localStorage.getItem('access_token')).toBeNull();
  expect(localStorage.getItem('refresh_token')).toBeNull();
});


it('does not refresh chat from a send completed after sign-out', async () => {
  localStorage.setItem('auth', JSON.stringify({ token: 'synthetic-session', email: 'synthetic@example.invalid', userId: 'synthetic-user' }));
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByText('synthetic@example.invalid');
  let finish: (response: Response) => void = () => {};
  const requests = vi.fn((input: string, init?: RequestInit) => init?.method === 'POST'
    ? new Promise<Response>(resolve => { finish = resolve; })
    : Promise.resolve(new Response(JSON.stringify(input.includes('/messages') ? [] : { contributors: [] }))));
  vi.stubGlobal('fetch', requests);
  fireEvent.click(screen.getByRole('button', { name: /discussion/i }));
  await act(async () => {});
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Synthetic message' } });
  const form = screen.getByRole('textbox').closest('form');
  if (!form) throw new Error('Missing chat form');
  fireEvent.submit(form);
  fireEvent.click(projectButton(/sign out/i));
  await act(async () => {});
  const afterLogout = requests.mock.calls.length;
  await act(async () => { finish(new Response('{}')); });
  expect(requests).toHaveBeenCalledTimes(afterLogout);
});

it.each(['legacy', 'oauth', 'denied'] as const)('keeps %s storage safe during mount and later events', async mode => {
  mockProject('active', null);
  if (mode === 'legacy') localStorage.setItem('auth', JSON.stringify({ token: 1, email: null, userId: [] }));
  if (mode === 'oauth') localStorage.setItem('access_token', 'malformed');
  if (mode === 'denied') vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError'); });
  render(<ProjectPage />);
  await screen.findByRole('heading', { name: 'Example Project' });
  act(() => window.dispatchEvent(new Event('storage')));
  expect(projectButton(/sign in/i)).toBeTruthy();
  for (const [input, init] of vi.mocked(fetch).mock.calls) {
    if (String(input).endsWith('/contributors')) expect(new Headers(init?.headers).has('Authorization')).toBe(false);
  }
});


it('does not read browser authentication during server rendering', () => {
  const read = vi.spyOn(Storage.prototype, 'getItem');
  expect(renderToString(<ProjectPage />)).toContain('Loading...');
  expect(read).not.toHaveBeenCalled();
});

it('does not clear a newer OAuth session installed while signing out', async () => {
  localStorage.setItem('auth', JSON.stringify({ token: 'synthetic-old', email: 'old@example.invalid', userId: 'old-user' }));
  localStorage.setItem('access_token', 'synthetic-old');
  localStorage.setItem('refresh_token', 'synthetic-old-refresh');
  mockProject('active', null);
  render(<ProjectPage />);
  await screen.findByText('old@example.invalid');
  const remove = Storage.prototype.removeItem;
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(function (this: Storage, key: string) {
    remove.call(this, key);
    if (key === 'auth') {
      this.setItem('access_token', `synthetic.${btoa(JSON.stringify({ sub: 'new-user', email: 'new@example.invalid' }))}.fixture`);
      this.setItem('refresh_token', 'synthetic-new-refresh');
    }
  });
  fireEvent.click(projectButton(/sign out/i));
  await screen.findByText('new@example.invalid');
  expect(localStorage.getItem('refresh_token')).toBe('synthetic-new-refresh');
});


it('ignores a late project response after navigation', async () => {
  let finish: (response: Response) => void = () => {};
  const project = { id: 'next-project', title: 'Next project', description: '', category: 'tools', status: 'active', repo_url: null, deploy_url: null, tech_stack: [], agent_name: 'Demo', agent_handle: 'demo', creator_agent_id: 'demo-agent', votes_up: 0, votes_down: 0, created_at: '2026-10-01T00:00:00Z' };
  vi.stubGlobal('fetch', vi.fn((input: string) => {
    if (input.endsWith('/demo-project')) return new Promise<Response>(resolve => { finish = resolve; });
    return Promise.resolve(new Response(JSON.stringify(input.endsWith('/ownership') ? null : input.endsWith('/contributors') ? { contributors: [] } : project)));
  }));
  const view = render(<ProjectPage />);
  route.id = 'next-project';
  view.rerender(<ProjectPage />);
  await screen.findByRole('heading', { name: 'Next project' });
  await act(async () => { finish(new Response(JSON.stringify({ ...project, id: 'demo-project', title: 'Stale project' }))); });
  expect(screen.getByRole('heading', { name: 'Next project' })).toBeTruthy();
  expect(screen.queryByText('Stale project')).toBeNull();
});
