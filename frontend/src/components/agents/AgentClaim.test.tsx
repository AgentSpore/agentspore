// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, act } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AgentClaim } from './AgentClaim';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
const agentId = '11111111-1111-1111-1111-111111111111';
const claim = { claim_id: 'request', agent_id: agentId, agent_name: 'Synthetic agent', user_id: 'owner', user_name: 'Synthetic owner', status: 'pending', expires_at: '2026-10-27T23:00:00Z' };
let verified = true;
function response(value: unknown) { return new Response(JSON.stringify(value)); }
function fixture(input: RequestInfo | URL, options?: RequestInit) {
  const path = String(input);
  if (path.endsWith('/auth/me')) return Promise.resolve(response({ id: 'owner', name: 'Synthetic owner', is_verified: verified }));
  if (path.includes('/agent-claims')) return Promise.resolve(response({ ...claim, status: options?.method === 'DELETE' ? 'canceled' : options?.method === 'POST' ? 'pending' : 'claimed' }));
  return Promise.resolve(response({ id: agentId, name: 'Synthetic agent' }));
}
beforeEach(() => { verified = true; const storage = new Map<string, string>([['access_token', 'synthetic-access']]); vi.stubGlobal('localStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key), clear: () => storage.clear() }); vi.stubGlobal('fetch', vi.fn(fixture)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it.each(['en', 'ru'] as const)('creates explicitly, keeps exact proof private to agent, then reads success in %s', async locale => {
  render(<LocaleProvider initialLocale={locale}><AgentClaim agentId={agentId} /></LocaleProvider>);
  const create = await screen.findByRole('button', { name: locale === 'en' ? 'Request ownership' : 'Запросить привязку' });
  expect(vi.mocked(fetch).mock.calls.every(([, options]) => !options?.method)).toBe(true);
  fireEvent.click(create);
  const instruction = await screen.findByRole('textbox');
  expect(instruction.getAttribute('readonly')).not.toBeNull();
  expect(instruction.textContent).toContain('"expected_user_id":"owner"');
  expect(instruction.textContent).not.toContain('synthetic-access');
  fireEvent.click(screen.getByRole('button', { name: locale === 'en' ? 'Check status' : 'Проверить статус' }));
  expect(await screen.findByRole('link', { name: locale === 'en' ? 'Open workspace' : 'Открыть рабочую область' })).toBeTruthy();
});
it('blocks an unverified account and anonymous creation', async () => {
  verified = false; const view = render(<AgentClaim agentId={agentId} />);
  expect(await screen.findByText(/Verify your account email/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Request ownership' })).toBeNull();
  view.unmount(); localStorage.clear(); render(<AgentClaim agentId={agentId} />);
  expect(await screen.findByRole('link', { name: 'Sign in to continue' })).toBeTruthy();
});
it.each(['expired', 'denied', 'canceled'])('shows terminal %s state without success', async status => {
  vi.mocked(fetch).mockImplementation((input, options) => options?.method === 'POST' ? Promise.resolve(response({ ...claim, status })) : fixture(input, options));
  render(<AgentClaim agentId={agentId} />); fireEvent.click(await screen.findByRole('button', { name: 'Request ownership' }));
  await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/expired|declined|canceled/));
  expect(screen.queryByRole('link', { name: 'Open workspace' })).toBeNull();
});
it('recovers an uncertain mutation through status read and rejects late results after logout', async () => {
  render(<AgentClaim agentId={agentId} />); fireEvent.click(await screen.findByRole('button', { name: 'Request ownership' }));
  await screen.findByRole('textbox'); vi.mocked(fetch).mockRejectedValueOnce(new Error('network'));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel request' }));
  expect(await screen.findByText(/Could not confirm the result/)).toBeTruthy();
  let release: (value: Response) => void = () => {};
  vi.mocked(fetch).mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: 'Check status' }));
  await act(async () => { localStorage.clear(); window.dispatchEvent(new Event('storage')); release(response({ ...claim, status: 'claimed' })); });
  expect(await screen.findByRole('link', { name: 'Sign in to continue' })).toBeTruthy();
  expect(screen.queryByRole('link', { name: 'Open workspace' })).toBeNull();
});

it('shows ownership conflict and retries a lost create response without exposing an approval instruction', async () => {
  render(<AgentClaim agentId={agentId} />);
  const create = await screen.findByRole('button', { name: 'Request ownership' });
  vi.mocked(fetch).mockResolvedValueOnce(new Response('', { status: 409 })); fireEvent.click(create);
  expect(await screen.findByText(/The agent may already be linked/)).toBeTruthy(); expect(screen.queryByRole('textbox')).toBeNull();
  vi.mocked(fetch).mockRejectedValueOnce(new Error('network')); fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(await screen.findByText(/Could not confirm the result/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Try again' })); await screen.findByRole('textbox');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel request' }));
  expect(await screen.findByText('Request canceled')).toBeTruthy(); expect(screen.queryByRole('textbox')).toBeNull();
});
