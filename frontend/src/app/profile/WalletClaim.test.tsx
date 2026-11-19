// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProfilePage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';
import type { SolanaProvider } from '@/lib/solanaWalletClaim';

vi.mock('next/navigation', () => ({ usePathname: () => '/profile', useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }) }));
const WALLET = '4Nd1mYQzvgvbUQvTzxWCvJGZaAF5bYhQ7pTvVxkXvQfM';
const MESSAGE = 'AgentSpore wallet claim\nnonce: synthetic-123';
const SIGNATURE = Uint8Array.from({ length: 64 }, (_, i) => i);
const SIGNATURE_B64 = btoa(String.fromCharCode(...SIGNATURE));
const owner = (wallet: string | null) => ({ id: 'u1', name: 'Synthetic owner', email: 'owner@example.invalid', avatar_url: null, token_balance: 0, solana_wallet: wallet, is_admin: false, created_at: '2026-01-01' });

const OTHER = '9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin';
let meWallet: string | null;
let patchOk: boolean;
let claimResponse: () => Response;
let challengeResponse: () => Response;
let calls: string[];

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } });
  localStorage.setItem('access_token', 'synthetic-session');
  meWallet = null;
  patchOk = false;
  calls = [];
  challengeResponse = () => Response.json({ message: MESSAGE, expires_at: '2026-01-01T00:05:00Z' });
  claimResponse = () => { meWallet = WALLET; return Response.json({ solana_wallet: WALLET, status: 'connected' }); };
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    calls.push(`${init?.method ?? 'GET'} ${path.split('/api/v1')[1]}`);
    if (path.endsWith('/solana-wallet') && init?.method === 'PATCH') {
      if (!patchOk) return Response.json({ detail: 'Solana wallet already connected to another account' }, { status: 409 });
      meWallet = OTHER;
      return Response.json({ solana_wallet: OTHER, status: 'connected' });
    }
    if (path.endsWith('/solana-wallet') && init?.method === 'DELETE') { meWallet = null; return new Response(null, { status: 200 }); }
    if (path.endsWith('/solana-wallet/challenge')) return challengeResponse();
    if (path.endsWith('/solana-wallet/claim')) return claimResponse();
    if (path.endsWith('/rewards/welcome')) return Response.json({ state: 'disabled', reason: 'program_disabled', amount_raw: null, decimals: 6, asset: 'ASPORE', next_action: 'none', hold_reasons: [], payout: null, wait_until: null, actions: [] });
    if (path.endsWith('/auth/me')) return Response.json(owner(meWallet));
    if (path.endsWith('/users/me/aspore')) return Response.json({ aspore_balance: 0 });
    return Response.json([]);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete window.phantom; delete window.solflare; delete window.solana; });

function installWallet(address = WALLET, signMessage: NonNullable<SolanaProvider['signMessage']> = vi.fn(async () => ({ signature: SIGNATURE }))) {
  const connect = vi.fn(async () => ({ publicKey: { toString: () => address } }));
  window.phantom = { solana: { connect, signMessage } };
  return { connect, signMessage };
}

async function submitTakenAddress(locale: 'en' | 'ru' = 'en') {
  render(<LocaleProvider initialLocale={locale}><ProfilePage /></LocaleProvider>);
  const input = await screen.findByLabelText(accountMessages[locale].solanaWalletAddress);
  fireEvent.change(input, { target: { value: WALLET } });
  fireEvent.click(screen.getByRole('button', { name: accountMessages[locale].connect }));
  await screen.findByText(accountMessages[locale].walletTaken);
}

const clickClaim = (locale: 'en' | 'ru' = 'en') => fireEvent.click(screen.getByRole('button', { name: accountMessages[locale].walletClaimButton }));

it('offers wallet confirmation on 409 instead of a red error', async () => {
  installWallet();
  await submitTakenAddress();
  expect(screen.getByRole('button', { name: 'Confirm with wallet' })).toBeTruthy();
  expect(screen.queryByText(accountMessages.en.solanaWalletAlreadyConnectedToAnotherAccount)).toBeNull();
});

it('signs the exact challenge message and claims with the base64 signature', async () => {
  const { signMessage } = installWallet();
  await submitTakenAddress();
  clickClaim();
  expect(await screen.findByText('Connected wallet')).toBeTruthy();
  const [bytes, display] = vi.mocked(signMessage).mock.calls[0] as unknown as [Uint8Array, string];
  expect(new TextDecoder().decode(bytes)).toBe(MESSAGE);
  expect(display).toBe('utf8');
  const claimCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/solana-wallet/claim'));
  expect(JSON.parse(String(claimCall?.[1]?.body))).toEqual({ solana_wallet: WALLET, signature: SIGNATURE_B64 });
  expect(calls.indexOf('POST /users/solana-wallet/challenge')).toBeLessThan(calls.indexOf('POST /users/solana-wallet/claim'));
});

it('refuses a connected wallet with another address and does not claim', async () => {
  installWallet(OTHER);
  await submitTakenAddress();
  clickClaim();
  expect(await screen.findByText(accountMessages.en.walletClaimWrongWallet)).toBeTruthy();
  expect(calls).not.toContain('POST /users/solana-wallet/claim');
  expect((screen.getByRole('button', { name: 'Confirm with wallet' }) as HTMLButtonElement).disabled).toBe(false);
});

it('shows a neutral message when the user rejects the signature', async () => {
  installWallet(WALLET, vi.fn(async () => { throw Object.assign(new Error('User rejected the request.'), { code: 4001 }); }));
  await submitTakenAddress();
  clickClaim();
  const note = await screen.findByText(accountMessages.en.walletClaimCancelled);
  expect(note.closest('[role="alert"]')).toBeNull();
  expect(calls).not.toContain('POST /users/solana-wallet/claim');
  expect((screen.getByRole('button', { name: 'Confirm with wallet' }) as HTMLButtonElement).disabled).toBe(false);
});

it('shows a hint when no wallet extension is present', async () => {
  await submitTakenAddress();
  clickClaim();
  expect(await screen.findByText(accountMessages.en.walletClaimNoProvider)).toBeTruthy();
  expect(calls).not.toContain('POST /users/solana-wallet/challenge');
});

it('asks to retry when the confirmation expired', async () => {
  claimResponse = () => Response.json({ detail: 'Confirmation expired. Request a new one.' }, { status: 400 });
  installWallet();
  await submitTakenAddress();
  clickClaim();
  expect(await screen.findByText(accountMessages.en.walletClaimExpired)).toBeTruthy();
  await waitFor(() => expect((screen.getByRole('button', { name: 'Confirm with wallet' }) as HTMLButtonElement).disabled).toBe(false));
});

it('renders the Russian texts', async () => {
  installWallet(WALLET, vi.fn(async () => { throw Object.assign(new Error('User rejected'), { code: 4001 }); }));
  await submitTakenAddress('ru');
  expect(screen.getByText(accountMessages.ru.walletTaken)).toBeTruthy();
  clickClaim('ru');
  expect(await screen.findByText(accountMessages.ru.walletClaimCancelled)).toBeTruthy();
});

it('accepts a signMessage that resolves a bare Uint8Array', async () => {
  installWallet(WALLET, vi.fn(async () => SIGNATURE));
  await submitTakenAddress();
  clickClaim();
  expect(await screen.findByText('Connected wallet')).toBeTruthy();
  const claimCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/solana-wallet/claim'));
  expect(JSON.parse(String(claimCall?.[1]?.body)).signature).toBe(SIGNATURE_B64);
});

it('falls back to provider.publicKey when connect resolves nothing', async () => {
  const signMessage = vi.fn(async () => ({ signature: SIGNATURE }));
  window.phantom = { solana: { connect: vi.fn(async () => undefined), publicKey: { toString: () => WALLET }, signMessage } };
  await submitTakenAddress();
  clickClaim();
  expect(await screen.findByText('Connected wallet')).toBeTruthy();
});

it('uses window.solflare when Phantom is absent', async () => {
  const signMessage = vi.fn(async () => ({ signature: SIGNATURE }));
  window.solflare = { connect: vi.fn(async () => undefined), publicKey: { toString: () => WALLET }, signMessage };
  await submitTakenAddress();
  clickClaim();
  expect(await screen.findByText('Connected wallet')).toBeTruthy();
  expect(signMessage).toHaveBeenCalledOnce();
});

it('shows the signature mismatch message on a 400 from the server', async () => {
  claimResponse = () => Response.json({ detail: 'Signature does not match this wallet.' }, { status: 400 });
  installWallet();
  await submitTakenAddress();
  clickClaim();
  expect(await screen.findByText(accountMessages.en.walletClaimBadSignature)).toBeTruthy();
});

it('shows the rate limit message on a 429 from the claim', async () => {
  claimResponse = () => new Response('{}', { status: 429 });
  installWallet();
  await submitTakenAddress();
  clickClaim();
  expect(await screen.findByText(accountMessages.en.walletClaimRateLimited)).toBeTruthy();
});

it('announces the taken notice and links it to the address input', async () => {
  installWallet();
  await submitTakenAddress();
  const notice = screen.getByText(accountMessages.en.walletTaken);
  expect(notice.getAttribute('role')).toBe('alert');
  const input = screen.getByLabelText(accountMessages.en.solanaWalletAddress);
  expect(input.getAttribute('aria-describedby')?.split(' ')).toContain(notice.id);
});

it('does not show the confirmed notice for a later address that was not confirmed', async () => {
  installWallet();
  await submitTakenAddress();
  clickClaim();
  expect(await screen.findByText(accountMessages.en.walletClaimDone)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: accountMessages.en.disconnect }));
  const input = await screen.findByLabelText(accountMessages.en.solanaWalletAddress);
  patchOk = true;
  fireEvent.change(input, { target: { value: OTHER } });
  fireEvent.click(screen.getByRole('button', { name: accountMessages.en.connect }));
  expect(await screen.findByText(OTHER)).toBeTruthy();
  expect(screen.queryByText(accountMessages.en.walletClaimDone)).toBeNull();
});

it('locks the address input while the claim waits for the wallet', async () => {
  let release: (value: { signature: Uint8Array }) => void = () => undefined;
  installWallet(WALLET, vi.fn(() => new Promise<{ signature: Uint8Array }>(resolve => { release = resolve; })));
  await submitTakenAddress();
  clickClaim();
  const input = screen.getByLabelText(accountMessages.en.solanaWalletAddress) as HTMLInputElement;
  await waitFor(() => expect(input.disabled).toBe(true));
  expect((screen.getByRole('button', { name: accountMessages.en.connect }) as HTMLButtonElement).disabled).toBe(true);
  release({ signature: SIGNATURE });
  expect(await screen.findByText('Connected wallet')).toBeTruthy();
});

it('resets the confirmed notice on a plain save even when no disconnect happened', async () => {
  installWallet();
  await submitTakenAddress();
  claimResponse = () => Response.json({ solana_wallet: WALLET, status: 'connected' });
  clickClaim();
  await waitFor(() => {
    expect(screen.queryByText(accountMessages.en.walletTaken)).toBeNull();
    expect(screen.queryByText('Connected wallet')).toBeNull();
    expect(screen.queryByLabelText(accountMessages.en.solanaWalletAddress)).not.toBeNull();
  });
  patchOk = true;
  fireEvent.change(screen.getByLabelText(accountMessages.en.solanaWalletAddress), { target: { value: OTHER } });
  fireEvent.click(screen.getByRole('button', { name: accountMessages.en.connect }));
  expect(await screen.findByText(OTHER)).toBeTruthy();
  expect(screen.queryByText(accountMessages.en.walletClaimDone)).toBeNull();
});

it('treats a wallet that never answers as cancelled and frees the button', async () => {
  const signMessage = vi.fn(() => new Promise<{ signature: Uint8Array }>(() => undefined));
  installWallet(WALLET, signMessage);
  await submitTakenAddress();
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  try {
    clickClaim();
    for (let i = 0; i < 50 && signMessage.mock.calls.length === 0; i++) await vi.advanceTimersByTimeAsync(10);
    expect(signMessage).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(120_000);
  } finally { vi.useRealTimers(); }
  expect(await screen.findByText(accountMessages.en.walletClaimCancelled)).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Confirm with wallet' }) as HTMLButtonElement).disabled).toBe(false);
});
