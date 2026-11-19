import { fetchWithAuth } from '@/lib/auth';

export interface SolanaProvider {
  publicKey?: { toString(): string } | null;
  connect(): Promise<{ publicKey?: { toString(): string } | null } | void>;
  signMessage?: (message: Uint8Array, display?: string) => Promise<{ signature: Uint8Array } | Uint8Array>;
}

export type ClaimResult =
  | { kind: 'ok' }
  | { kind: 'no_provider' | 'wrong_wallet' | 'cancelled' | 'expired' | 'bad_signature' | 'rate_limited' | 'error' };

declare global {
  interface Window { phantom?: { solana?: SolanaProvider }; solflare?: SolanaProvider; solana?: SolanaProvider }
}

/** First injected Solana wallet that can sign messages: Phantom, Solflare, then the generic namespace. */
export function findSolanaProvider(): SolanaProvider | null {
  if (typeof window === 'undefined') return null;
  return [window.phantom?.solana, window.solflare, window.solana].find(p => typeof p?.signMessage === 'function') ?? null;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

const WALLET_TIMEOUT_MS = 120_000;
const WALLET_TIMEOUT = Symbol('wallet-timeout');

/** A wallet popup nobody answers must not keep the page busy forever. */
async function withinWalletTimeout<T>(pending: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expiry = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(WALLET_TIMEOUT), WALLET_TIMEOUT_MS); });
  try { return await Promise.race([pending, expiry]); } finally { clearTimeout(timer); }
}

const isUserRejection = (e: unknown) =>
  typeof e === 'object' && e !== null && ((e as { code?: unknown }).code === 4001 || /reject|denied|cancel/i.test(String((e as { message?: unknown }).message)));

async function post(url: string, body: object): Promise<Response> {
  return fetchWithAuth(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

async function connectedAddress(provider: SolanaProvider): Promise<string | null> {
  const connected = await provider.connect();
  const key = (connected && connected.publicKey) || provider.publicKey;
  return key ? key.toString() : null;
}

/** Challenge, wallet signature, claim. The signature only goes into the claim request body. */
export async function claimWalletWithSignature(args: { apiUrl: string; address: string }): Promise<ClaimResult> {
  const provider = findSolanaProvider();
  if (!provider?.signMessage) return { kind: 'no_provider' };
  const base = `${args.apiUrl}/api/v1/users/solana-wallet`;
  try {
    const challenge = await post(`${base}/challenge`, { solana_wallet: args.address });
    if (challenge.status === 429) return { kind: 'rate_limited' };
    const { message } = await challenge.json().catch(() => ({}));
    if (!challenge.ok || typeof message !== 'string') return { kind: 'error' };
    if (await withinWalletTimeout(connectedAddress(provider)) !== args.address) return { kind: 'wrong_wallet' };
    const signed = await withinWalletTimeout(provider.signMessage(new TextEncoder().encode(message), 'utf8'));
    const signature = bytesToBase64(signed instanceof Uint8Array ? signed : signed.signature);
    const claim = await post(`${base}/claim`, { solana_wallet: args.address, signature });
    if (claim.ok) return { kind: 'ok' };
    if (claim.status === 429) return { kind: 'rate_limited' };
    if (claim.status !== 400) return { kind: 'error' };
    const { detail } = await claim.json().catch(() => ({}));
    return { kind: typeof detail === 'string' && /expired/i.test(detail) ? 'expired' : 'bad_signature' };
  } catch (e) {
    return { kind: e === WALLET_TIMEOUT || isUserRejection(e) ? 'cancelled' : 'error' };
  }
}
