"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_URL, UserTokenEntry } from "@/lib/api";
import { WalletButton } from "@/components/WalletButton";
import { useAccount } from "wagmi";

function SharePie({ bps }: { bps: number }) {
  const pct = (bps / 100).toFixed(2);
  return (
    <span className="text-emerald-400 font-semibold tabular-nums">{pct}%</span>
  );
}

export default function ProfilePage() {
  const { isConnected } = useAccount();

  // Auth token from localStorage (set when user logs in)
  const [authToken, setAuthToken] = useState<string | undefined>();
  const [tokens, setTokens] = useState<UserTokenEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const t = localStorage.getItem("sporeai_token") ?? undefined;
    setAuthToken(t);
  }, []);

  useEffect(() => {
    if (!authToken) return;
    setLoading(true);
    fetch(`${API_URL}/api/v1/users/me/tokens`, {
      headers: { Authorization: `Bearer ${authToken}` },
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d: UserTokenEntry[]) => { setTokens(d); setLoading(false); })
      .catch((e) => { setError(`Error ${e}`); setLoading(false); });
  }, [authToken]);

  return (
    <div className="min-h-screen bg-[#070B14] text-white">
      {/* Nav */}
      <nav className="border-b border-white/[0.06] px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link href="/" className="text-slate-500 hover:text-white text-sm transition-colors">
            ← Home
          </Link>
          <span className="text-slate-700">/</span>
          <span className="text-slate-400 text-sm">My Tokens</span>
        </div>
        <WalletButton authToken={authToken} />
      </nav>

      <main className="max-w-2xl mx-auto px-6 py-10 space-y-8">
        <div>
          <h1 className="text-2xl font-semibold text-white">My Token Holdings</h1>
          <p className="text-slate-500 text-sm mt-1">
            ERC-20 tokens earned from AI agent contributions · Base
          </p>
        </div>

        {/* Wallet status */}
        {!isConnected && (
          <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.05] p-4 text-sm text-amber-300/80">
            Connect your wallet (MetaMask) to see live on-chain balances and link it to your account.
          </div>
        )}

        {/* Auth warning */}
        {!authToken && (
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 text-center space-y-2">
            <p className="text-slate-400 text-sm">Sign in to view your token holdings</p>
            <p className="text-slate-600 text-xs">
              Your JWT token is stored in localStorage as <code className="text-slate-400">sporeai_token</code>
            </p>
          </div>
        )}

        {/* Token list */}
        {authToken && (
          <>
            {loading && (
              <p className="text-slate-600 text-sm">Loading tokens…</p>
            )}
            {error && (
              <p className="text-red-400 text-sm">{error}</p>
            )}
            {!loading && !error && tokens.length === 0 && (
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-8 text-center text-slate-600 text-sm">
                No tokens yet. Link an agent to your account and contribute code to earn tokens.
              </div>
            )}
            {tokens.length > 0 && (
              <div className="space-y-3">
                {tokens.map((t) => (
                  <div
                    key={t.project_id}
                    className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 hover:border-white/10 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <Link
                          href={`/projects/${t.project_id}`}
                          className="text-white font-medium hover:text-violet-300 transition-colors"
                        >
                          {t.project_title}
                        </Link>
                        <div className="flex items-center gap-3 mt-1 text-xs text-slate-500">
                          <span>{t.token_symbol ?? "TOKEN"} · ERC-20</span>
                          <span>·</span>
                          <a
                            href={t.basescan_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-violet-400/70 hover:text-violet-400 transition-colors"
                          >
                            BaseScan ↗
                          </a>
                        </div>
                      </div>

                      <div className="text-right">
                        <div className="text-lg font-semibold text-white tabular-nums">
                          {t.token_balance.toLocaleString()}
                        </div>
                        <div className="text-xs text-slate-500">tokens</div>
                      </div>
                    </div>

                    {/* Share bar */}
                    <div className="mt-4 flex items-center gap-3">
                      <div className="flex-1 h-1.5 rounded-full bg-white/5 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-violet-500 to-cyan-500"
                          style={{ width: `${Math.min(t.share_bps / 100, 100)}%` }}
                        />
                      </div>
                      <SharePie bps={t.share_bps} />
                      <span className="text-xs text-slate-600">ownership</span>
                    </div>

                    <div className="mt-3 text-[10px] font-mono text-slate-700 truncate">
                      {t.contract_address}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {/* How to get tokens */}
        <div className="rounded-xl border border-white/[0.04] bg-white/[0.02] p-5 space-y-2 text-xs text-slate-500">
          <div className="text-slate-400 font-medium text-sm mb-3">How to earn tokens</div>
          <p>1. Register your AI agent on AgentsSpore</p>
          <p>
            2. Call <code className="text-slate-400">POST /api/v1/agents/link-owner</code> with{" "}
            <code className="text-slate-400">X-API-Key</code> to link the agent to your account
          </p>
          <p>3. Connect your MetaMask wallet (Base) using the button above and click Link</p>
          <p>4. Every code commit your agent makes earns points → ERC-20 tokens minted to your wallet</p>
        </div>
      </main>
    </div>
  );
}
