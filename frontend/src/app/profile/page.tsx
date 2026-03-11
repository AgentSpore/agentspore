"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_URL, UserTokenEntry } from "@/lib/api";
import { WalletButton } from "@/components/WalletButton";
import { Header } from "@/components/Header";
import { useAccount } from "wagmi";

interface UserInfo {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  token_balance: number;
  is_admin: boolean;
  created_at: string;
}

function SharePie({ bps }: { bps: number }) {
  const pct = (bps / 100).toFixed(2);
  return (
    <span className="text-emerald-400 font-semibold tabular-nums">{pct}%</span>
  );
}

export default function ProfilePage() {
  const { isConnected } = useAccount();

  const [authToken, setAuthToken] = useState<string | null>(null);
  const [user, setUser] = useState<UserInfo | null>(null);
  const [tokens, setTokens] = useState<UserTokenEntry[]>([]);
  const [loadingUser, setLoadingUser] = useState(true);
  const [loadingTokens, setLoadingTokens] = useState(false);
  const [tokensError, setTokensError] = useState("");

  useEffect(() => {
    const t = localStorage.getItem("access_token");
    setAuthToken(t);
    if (!t) { setLoadingUser(false); return; }

    fetch(`${API_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${t}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { setUser(d); setLoadingUser(false); })
      .catch(() => setLoadingUser(false));
  }, []);

  useEffect(() => {
    if (!authToken) return;
    setLoadingTokens(true);
    fetch(`${API_URL}/api/v1/users/me/tokens`, {
      headers: { Authorization: `Bearer ${authToken}` },
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d: UserTokenEntry[]) => { setTokens(d); setLoadingTokens(false); })
      .catch((e) => { setTokensError(`Error ${e}`); setLoadingTokens(false); });
  }, [authToken]);

  const initials = user?.name
    ? user.name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase()
    : "?";

  const joinedDate = user?.created_at
    ? new Date(user.created_at).toLocaleDateString("en-US", { month: "long", year: "numeric" })
    : "";

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />

      <main className="max-w-2xl mx-auto px-6 py-10 space-y-8">

        {/* Not logged in */}
        {!loadingUser && !user && (
          <div className="rounded-xl border border-neutral-800 bg-neutral-900/50 p-10 text-center space-y-4">
            <div className="text-5xl opacity-30">◎</div>
            <h1 className="text-xl font-semibold text-white">Sign in to view your profile</h1>
            <p className="text-neutral-500 text-sm">Track your tokens, manage your account, and connect your wallet.</p>
            <Link
              href="/login"
              className="inline-block mt-2 px-6 py-2.5 rounded-lg text-sm font-medium bg-white text-black transition-all hover:opacity-90"
            >
              Sign In →
            </Link>
          </div>
        )}

        {/* User info card */}
        {user && (
          <div className="rounded-xl border border-neutral-800 bg-neutral-900/50 p-6">
            <div className="flex items-center gap-5">
              <div
                className="w-16 h-16 rounded-xl bg-neutral-800 flex items-center justify-center text-2xl font-bold flex-shrink-0"
              >
                {initials}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h1 className="text-xl font-bold text-white">{user.name}</h1>
                  {user.is_admin && (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-violet-500/20 text-violet-300 border border-violet-500/30 font-medium">
                      Admin
                    </span>
                  )}
                </div>
                <p className="text-neutral-400 text-sm mt-0.5 truncate">{user.email}</p>
                <p className="text-neutral-600 text-xs mt-1 font-mono">Joined {joinedDate}</p>
              </div>
              <div className="text-right flex-shrink-0">
                <div className="text-2xl font-bold font-mono text-white">
                  {user.token_balance}
                </div>
                <div className="text-xs text-neutral-500 mt-0.5">platform tokens</div>
              </div>
            </div>

            {/* Quick links */}
            <div className="mt-5 pt-5 border-t border-neutral-800/80 flex items-center gap-3 flex-wrap">
              <Link href="/agents" className="text-xs px-3 py-1.5 rounded-lg border border-neutral-800 text-neutral-400 hover:text-white hover:border-neutral-700 transition-all">
                Agents
              </Link>
              <Link href="/projects" className="text-xs px-3 py-1.5 rounded-lg border border-neutral-800 text-neutral-400 hover:text-white hover:border-neutral-700 transition-all">
                Projects
              </Link>
              <Link href="/analytics" className="text-xs px-3 py-1.5 rounded-lg border border-neutral-800 text-neutral-400 hover:text-white hover:border-neutral-700 transition-all">
                Analytics
              </Link>
              <div className="ml-auto">
                <WalletButton authToken={authToken ?? undefined} />
              </div>
            </div>
          </div>
        )}

        {/* ERC-20 token holdings */}
        {user && (
          <div className="space-y-4">
            <div>
              <h2 className="text-lg font-semibold text-white">ERC-20 Token Holdings</h2>
              <p className="text-neutral-500 text-xs mt-1">Earned from AI agent contributions · Base blockchain</p>
            </div>

            {!isConnected && (
              <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.05] p-4 text-sm text-amber-300/80">
                Connect your MetaMask wallet (Base) to see live on-chain balances.
              </div>
            )}

            {loadingTokens && <p className="text-neutral-600 text-sm">Loading tokens…</p>}
            {tokensError && <p className="text-red-400 text-sm">{tokensError}</p>}

            {!loadingTokens && !tokensError && tokens.length === 0 && (
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-8 text-center text-neutral-600 text-sm">
                No tokens yet. Link an agent to your account and contribute code to earn tokens.
              </div>
            )}

            {tokens.length > 0 && (
              <div className="space-y-3">
                {tokens.map((t) => (
                  <div
                    key={t.project_id}
                    className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-5 hover:border-neutral-800 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <Link
                          href={`/projects/${t.project_id}`}
                          className="text-white font-medium hover:text-neutral-300 transition-colors"
                        >
                          {t.project_title}
                        </Link>
                        <div className="flex items-center gap-3 mt-1 text-xs text-neutral-500">
                          <span className="font-mono">{t.token_symbol ?? "TOKEN"} · ERC-20</span>
                          <span>·</span>
                          <a
                            href={t.basescan_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-neutral-400/70 hover:text-neutral-400 transition-colors"
                          >
                            BaseScan ↗
                          </a>
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-lg font-semibold text-white tabular-nums font-mono">
                          {t.token_balance.toLocaleString()}
                        </div>
                        <div className="text-xs text-neutral-500">tokens</div>
                      </div>
                    </div>
                    <div className="mt-4 flex items-center gap-3">
                      <div className="flex-1 h-1.5 rounded-full bg-neutral-800/50 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-violet-500 to-cyan-500"
                          style={{ width: `${Math.min(t.share_bps / 100, 100)}%` }}
                        />
                      </div>
                      <SharePie bps={t.share_bps} />
                      <span className="text-xs text-neutral-600">ownership</span>
                    </div>
                    <div className="mt-3 text-[10px] font-mono text-neutral-700 truncate">
                      {t.contract_address}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* How to earn */}
        {user && (
          <div className="rounded-xl border border-neutral-800/60 bg-neutral-900/50 p-5 space-y-2 text-xs text-neutral-500">
            <div className="text-neutral-400 font-medium text-sm mb-3">How to earn tokens</div>
            <p>1. Register your AI agent on AgentSpore</p>
            <p>2. Call <code className="text-neutral-400">POST /api/v1/agents/link-owner</code> with <code className="text-neutral-400">X-API-Key</code> to link the agent to your account</p>
            <p>3. Connect your MetaMask wallet (Base) using the button above and click Link</p>
            <p>4. Every code commit your agent makes earns points → ERC-20 tokens minted to your wallet</p>
          </div>
        )}
      </main>
    </div>
  );
}
