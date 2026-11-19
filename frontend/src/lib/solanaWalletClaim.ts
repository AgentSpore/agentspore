export interface SolanaProvider {
  publicKey?: { toString(): string } | null;
  connect(): Promise<{ publicKey?: { toString(): string } | null } | void>;
  signMessage?: (message: Uint8Array, display?: string) => Promise<{ signature: Uint8Array } | Uint8Array>;
}

export type ClaimResult =
  | { kind: 'ok' }
  | { kind: 'no_provider' | 'wrong_wallet' | 'cancelled' | 'expired' | 'bad_signature' | 'rate_limited' | 'error' };

declare global {
  interface Window { phantom?: { solana?: SolanaProvider }; solana?: SolanaProvider }
}

/** First injected Solana wallet that can sign messages: Phantom's namespace, then the generic one. */
export function findSolanaProvider(): SolanaProvider | null {
  if (typeof window === 'undefined') return null;
  return [window.phantom?.solana, window.solana].find(p => typeof p?.signMessage === 'function') ?? null;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

const isUserRejection = (e: unknown) =>
  typeof e === 'object' && e !== null && ((e as { code?: unknown }).code === 4001 || /reject|denied|cancel/i.test(String((e as { message?: unknown }).message)));

async function post(url: string, token: string, body: object): Promise<Response> {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
}

async function connectedAddress(provider: SolanaProvider): Promise<string | null> {
  const connected = await provider.connect();
  const key = (connected && connected.publicKey) || provider.publicKey;
  return key ? key.toString() : null;
}

/** Challenge, wallet signature, claim. The signature only goes into the claim request body. */
export async function claimWalletWithSignature(args: { apiUrl: string; token: string; address: string }): Promise<ClaimResult> {
  const provider = findSolanaProvider();
  if (!provider?.signMessage) return { kind: 'no_provider' };
  const base = `${args.apiUrl}/api/v1/users/solana-wallet`;
  try {
    const challenge = await post(`${base}/challenge`, args.token, { solana_wallet: args.address });
    if (challenge.status === 429) return { kind: 'rate_limited' };
    const { message } = await challenge.json().catch(() => ({}));
    if (!challenge.ok || typeof message !== 'string') return { kind: 'error' };
    if (await connectedAddress(provider) !== args.address) return { kind: 'wrong_wallet' };
    const signed = await provider.signMessage(new TextEncoder().encode(message), 'utf8');
    const signature = bytesToBase64(signed instanceof Uint8Array ? signed : signed.signature);
    const claim = await post(`${base}/claim`, args.token, { solana_wallet: args.address, signature });
    if (claim.ok) return { kind: 'ok' };
    if (claim.status === 429) return { kind: 'rate_limited' };
    if (claim.status !== 400) return { kind: 'error' };
    const { detail } = await claim.json().catch(() => ({}));
    return { kind: typeof detail === 'string' && /expired/i.test(detail) ? 'expired' : 'bad_signature' };
  } catch (e) {
    return { kind: isUserRejection(e) ? 'cancelled' : 'error' };
  }
}
