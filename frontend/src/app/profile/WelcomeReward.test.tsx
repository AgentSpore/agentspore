// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { welcomeRewardMessages } from '@/lib/i18n/welcomeReward';
import type { WelcomeRewardStatus } from '@/lib/welcome-reward';
import { WelcomeReward } from './WelcomeReward';
import type { ProfileRead } from './useProfileRead';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const funded: WelcomeRewardStatus = { state: 'frozen', reason: 'email_required', amount_raw: 10000000000 };
const view = (data: WelcomeRewardStatus): ProfileRead<WelcomeRewardStatus> => ({ state: { kind: 'success', data }, retry: vi.fn(), replace: vi.fn() });
function mount(data = funded, locale: 'en' | 'ru' = 'en') {
  const read = view(data);
  render(<LocaleProvider initialLocale={locale}><WelcomeReward read={read} token="synthetic-owner-a" /></LocaleProvider>);
  return read;
}
it.each(['en', 'ru'] as const)('shows disabled truthfully, without a promised balance in %s', locale => {
  mount({ state: 'disabled', reason: 'program_disabled', amount_raw: null }, locale);
  expect(screen.getByText(welcomeRewardMessages[locale].program_disabled)).toBeTruthy();
  expect(screen.queryByText(/10[ ,]000/)).toBeNull();
  expect(screen.queryByRole('button')).toBeNull();
});
it.each(['funding_unavailable', 'risk_review'] as const)('distinguishes conditional %s without a guaranteed amount', reason => {
  mount({ state: 'application', reason, amount_raw: null });
  expect(screen.getByText(welcomeRewardMessages.en[reason])).toBeTruthy();
  expect(screen.getByText(welcomeRewardMessages.en.conditional)).toBeTruthy();
  expect(screen.queryByText('ASPORE')).toBeNull();
});
it('does not describe an available entitlement as paid or spendable deposits', () => {
  mount({ state: 'available', reason: 'available', amount_raw: 123456789 });
  expect(screen.getByText(/123\.456789/)).toBeTruthy();
  expect(screen.getByText(welcomeRewardMessages.en.available)).toBeTruthy();
  expect(screen.getByText(welcomeRewardMessages.en.separate)).toBeTruthy();
});
it('renders retry and auth separately without inventing a zero balance', () => {
  const read: ProfileRead<WelcomeRewardStatus> = { state: { kind: 'error' }, retry: vi.fn(), replace: vi.fn() };
  const ui = render(<WelcomeReward read={read} token="synthetic-owner-a" />);
  fireEvent.click(within(screen.getByRole('alert')).getByRole('button'));
  expect(read.retry).toHaveBeenCalledOnce();
  ui.rerender(<WelcomeReward read={{ ...read, state: { kind: 'auth' } }} token="synthetic-owner-a" />);
  expect(screen.getByRole('link').getAttribute('href')).toBe('/login');
});
it('confirms the exact code without treating email confirmation as reward unlock', async () => {
  const calls = vi.fn(async (_url: string, _options: RequestInit) => new Response(null, { status: 202 }));
  vi.stubGlobal('fetch', calls);
  const read = mount();
  fireEvent.click(screen.getByRole('button', { name: 'Send confirmation code' }));
  const input = await screen.findByRole('textbox', { name: 'Confirmation code' });
  fireEvent.change(input, { target: { value: 'x'.repeat(43) } });
  calls.mockResolvedValueOnce(new Response(null, { status: 200 }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm email' }));
  expect(await screen.findByText(welcomeRewardMessages.en.confirmed)).toBeTruthy();
  expect(read.retry).toHaveBeenCalledOnce();
  expect(screen.queryByRole('textbox')).toBeNull();
  expect(calls.mock.calls[1]).toMatchObject([expect.stringContaining('/email-proof/confirm'), {
    method: 'POST', body: JSON.stringify({ code: 'x'.repeat(43) }), headers: { Authorization: 'Bearer synthetic-owner-a' },
  }]);
  expect(screen.queryByText(welcomeRewardMessages.en.state_available)).toBeNull();
});
it.each([429, 503])('shows safe request failure %s without reflecting backend text', async status => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'sensitive-code@example.invalid' }, { status })));
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Send confirmation code' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', welcomeRewardMessages.en[status === 429 ? 'cooldown' : 'unavailable']);
  expect(screen.queryByText('sensitive-code@example.invalid')).toBeNull();
});
it('aborts a pending code request when the owner changes, ignoring its late response', async () => {
  let resolve: (response: Response) => void = () => {};
  const call = vi.fn((_url: string, _options: RequestInit) => new Promise<Response>(done => { resolve = done; }));
  vi.stubGlobal('fetch', call);
  const ui = render(<WelcomeReward key="owner-a" read={view(funded)} token="synthetic-owner-a" />);
  fireEvent.click(screen.getByRole('button', { name: 'Send confirmation code' }));
  const signal = call.mock.calls[0]?.[1].signal;
  ui.rerender(<WelcomeReward key="owner-b" read={view({ state: 'disabled', reason: 'program_disabled', amount_raw: null })} token="synthetic-owner-b" />);
  expect(signal?.aborted).toBe(true);
  await act(async () => { resolve(new Response(null, { status: 202 })); });
  expect(screen.queryByRole('textbox')).toBeNull();
  expect(screen.queryByText(welcomeRewardMessages.en.sent)).toBeNull();
  expect(screen.queryByText(/10,000/)).toBeNull();
});

it('only creates an application after an explicit action, without promising funds', async () => {
  const result = { state: 'application', reason: 'risk_review', amount_raw: null, decimals: 6, asset: 'ASPORE', next_action: 'await_review' };
  const call = vi.fn(async () => Response.json(result));
  vi.stubGlobal('fetch', call);
  const read = mount({ state: 'not_applied', reason: 'not_applied', amount_raw: null });
  expect(call).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Submit application' }));
  await act(async () => {});
  expect(call).toHaveBeenCalledWith(expect.stringContaining('/welcome/application'), expect.objectContaining({ method: 'POST', headers: { Authorization: 'Bearer synthetic-owner-a' } }));
  expect(read.replace).toHaveBeenCalledWith({ state: 'application', reason: 'risk_review', amount_raw: null });
});
it('offers no recovery action for historical ineligible accounts', () => {
  mount({ state: 'not_applied', reason: 'not_eligible', amount_raw: null });
  expect(screen.queryByRole('button')).toBeNull();
});
it('keeps application failure recoverable and suppresses a late result after logout', async () => {
  let resolve: (value: Response) => void = () => {};
  const call = vi.fn(() => new Promise<Response>(done => { resolve = done; }));
  vi.stubGlobal('fetch', call);
  const read = view({ state: 'not_applied', reason: 'not_applied', amount_raw: null });
  const ui = render(<WelcomeReward key="a" read={read} token="synthetic-owner-a" />);
  fireEvent.click(screen.getByRole('button', { name: 'Submit application' }));
  await act(async () => { resolve(new Response(null, { status: 503 })); });
  expect(screen.getByRole('alert').textContent).toBe(welcomeRewardMessages.en.applyFailed);
  fireEvent.click(screen.getByRole('button', { name: 'Submit application' }));
  ui.unmount();
  await act(async () => { resolve(Response.json({ state: 'application', reason: 'risk_review', amount_raw: null, decimals: 6, asset: 'ASPORE', next_action: 'await_review' })); });
  expect(read.replace).not.toHaveBeenCalled();
});
