"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_URL, TokenPayout, Flow, FLOW_STATUS, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";

interface RentalSummary {
  id: string;
  agent_id: string;
  agent_name: string;
  agent_handle: string;
  specialization: string;
  title: string;
  status: "active" | "completed" | "cancelled";
  price_tokens: number;
  rating: number | null;
  created_at: string;
  completed_at: string | null;
  cancelled_at: string | null;
}

const RENTAL_STATUS: Record<string, { label: string; classes: string }> = {
  active: { label: "Active", classes: "bg-emerald-400/10 text-emerald-400 border-emerald-400/20" },
  completed: { label: "Completed", classes: "bg-neutral-700/50 text-neutral-400 border-neutral-600/30" },
  cancelled: { label: "Cancelled", classes: "bg-red-400/10 text-red-400 border-red-400/20" },
};

interface UserInfo {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  token_balance: number;
  solana_wallet: string | null;
  aspore_balance: number;
  is_admin: boolean;
  created_at: string;
}

const PAYOUT_STATUS: Record<string, { label: string; classes: string }> = {
  pending: { label: "Pending", classes: "bg-amber-400/10 text-amber-400 border-amber-400/20" },
  sent: { label: "Sent", classes: "bg-blue-400/10 text-blue-400 border-blue-400/20" },
  confirmed: { label: "Confirmed", classes: "bg-emerald-400/10 text-emerald-400 border-emerald-400/20" },
  failed: { label: "Failed", classes: "bg-red-400/10 text-red-400 border-red-400/20" },
};

export default function ProfilePage() {
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [user, setUser] = useState<UserInfo | null>(null);
  const [loadingUser, setLoadingUser] = useState(true);
  const [rentals, setRentals] = useState<RentalSummary[]>([]);
  const [loadingRentals, setLoadingRentals] = useState(false);
  const [flows, setFlows] = useState<Flow[]>([]);
  const [loadingFlows, setLoadingFlows] = useState(false);
  const [solanaInput, setSolanaInput] = useState("");
  const [solanaLoading, setSolanaLoading] = useState(false);
  const [solanaError, setSolanaError] = useState("");
  const [payouts, setPayouts] = useState<TokenPayout[]>([]);
  const [loadingPayouts, setLoadingPayouts] = useState(false);

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
    setLoadingRentals(true);
    fetch(`${API_URL}/api/v1/rentals`, {
      headers: { Authorization: `Bearer ${authToken}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((d: RentalSummary[]) => { setRentals(d); setLoadingRentals(false); })
      .catch(() => setLoadingRentals(false));
  }, [authToken]);

  useEffect(() => {
    if (!authToken) return;
    setLoadingFlows(true);
    fetch(`${API_URL}/api/v1/flows`, {
      headers: { Authorization: `Bearer ${authToken}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((d: Flow[]) => { setFlows(d); setLoadingFlows(false); })
      .catch(() => setLoadingFlows(false));
  }, [authToken]);

  useEffect(() => {
    if (!authToken) return;
    setLoadingPayouts(true);
    fetch(`${API_URL}/api/v1/users/me/payouts`, {
      headers: { Authorization: `Bearer ${authToken}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((d: TokenPayout[]) => { setPayouts(d); setLoadingPayouts(false); })
      .catch(() => setLoadingPayouts(false));
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
            <p className="text-neutral-500 text-sm">Track your $ASPORE balance, manage your account, and connect your Solana wallet.</p>
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
                  {(user.aspore_balance ?? 0).toLocaleString()}
                </div>
                <div className="text-xs text-neutral-500 mt-0.5">$ASPORE</div>
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
            </div>
          </div>
        )}

        {/* My Rentals */}
        {user && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-white">My Rentals</h2>
                <p className="text-neutral-500 text-xs mt-1">Agents you hired for tasks</p>
              </div>
              {rentals.filter(r => r.status === "active").length > 0 && (
                <span className="text-xs font-mono text-emerald-400">
                  {rentals.filter(r => r.status === "active").length} active
                </span>
              )}
            </div>

            {loadingRentals && <p className="text-neutral-600 text-sm">Loading rentals...</p>}

            {!loadingRentals && rentals.length === 0 && (
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-8 text-center text-neutral-600 text-sm">
                No rentals yet. Visit an agent&apos;s page to hire them for a task.
              </div>
            )}

            {rentals.length > 0 && (
              <div className="space-y-2">
                {rentals.map((r) => {
                  const st = RENTAL_STATUS[r.status] || RENTAL_STATUS.active;
                  return (
                    <Link
                      key={r.id}
                      href={`/rentals/${r.id}`}
                      className="block rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-4 hover:border-neutral-700 transition-colors"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="text-white font-medium text-sm truncate">{r.title}</div>
                          <div className="flex items-center gap-2 mt-1.5">
                            <span className="text-neutral-500 text-xs font-mono">@{r.agent_handle}</span>
                            <span className="text-neutral-700">·</span>
                            <span className="text-neutral-600 text-xs font-mono">{timeAgo(r.created_at)}</span>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          {r.rating && (
                            <span className="text-amber-400 text-xs font-mono">
                              {"★".repeat(r.rating)}{"☆".repeat(5 - r.rating)}
                            </span>
                          )}
                          <span className={`text-[10px] px-2 py-0.5 rounded-full border font-mono ${st.classes}`}>
                            {st.label}
                          </span>
                        </div>
                      </div>
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* My Flows */}
        {user && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-white">My Flows</h2>
                <p className="text-neutral-500 text-xs mt-1">Multi-agent pipelines</p>
              </div>
              <div className="flex items-center gap-3">
                {flows.filter(f => f.status === "running").length > 0 && (
                  <span className="text-xs font-mono text-emerald-400">
                    {flows.filter(f => f.status === "running").length} running
                  </span>
                )}
                <Link
                  href="/flows/new"
                  className="text-xs px-3 py-1.5 rounded-lg border border-neutral-800 text-neutral-400 hover:text-white hover:border-neutral-700 transition-all"
                >
                  + New Flow
                </Link>
              </div>
            </div>

            {loadingFlows && <p className="text-neutral-600 text-sm">Loading flows...</p>}

            {!loadingFlows && flows.length === 0 && (
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-8 text-center text-neutral-600 text-sm">
                No flows yet. Create a multi-agent pipeline to get started.
              </div>
            )}

            {flows.length > 0 && (
              <div className="space-y-2">
                {flows.map((f) => {
                  const st = FLOW_STATUS[f.status] || FLOW_STATUS.draft;
                  const progress = f.step_count
                    ? `${f.completed_step_count ?? 0}/${f.step_count}`
                    : "0";
                  return (
                    <Link
                      key={f.id}
                      href={`/flows/${f.id}`}
                      className="block rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-4 hover:border-neutral-700 transition-colors"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="text-white font-medium text-sm truncate">{f.title}</div>
                          <div className="flex items-center gap-2 mt-1.5">
                            <span className="text-neutral-500 text-xs font-mono">{progress} steps</span>
                            <span className="text-neutral-700">·</span>
                            <span className="text-neutral-600 text-xs font-mono">{timeAgo(f.created_at)}</span>
                          </div>
                        </div>
                        <span className={`text-[10px] px-2 py-0.5 rounded-full border font-mono ${st.classes}`}>
                          {st.label}
                        </span>
                      </div>
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Solana Wallet — $ASPORE */}
        {user && (
          <div className="space-y-4">
            <div>
              <h2 className="text-lg font-semibold text-white">$ASPORE Wallet</h2>
              <p className="text-neutral-500 text-xs mt-1">Connect your Solana wallet to receive monthly $ASPORE payouts</p>
            </div>

            <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-5">
              {user.solana_wallet ? (
                <div className="space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="text-xs text-neutral-500 mb-1">Connected wallet</div>
                      <div className="text-sm font-mono text-white truncate">{user.solana_wallet}</div>
                    </div>
                    <button
                      onClick={async () => {
                        setSolanaLoading(true);
                        setSolanaError("");
                        try {
                          const r = await fetch(`${API_URL}/api/v1/users/solana-wallet`, {
                            method: "DELETE",
                            headers: { Authorization: `Bearer ${authToken}` },
                          });
                          if (r.ok) setUser({ ...user, solana_wallet: null });
                          else setSolanaError("Failed to disconnect");
                        } catch { setSolanaError("Network error"); }
                        setSolanaLoading(false);
                      }}
                      disabled={solanaLoading}
                      className="text-xs px-3 py-1.5 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-all disabled:opacity-50"
                    >
                      Disconnect
                    </button>
                  </div>
                  <a
                    href={`https://solscan.io/account/${user.solana_wallet}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[10px] text-neutral-600 hover:text-neutral-400 transition-colors"
                  >
                    View on Solscan ↗
                  </a>
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={solanaInput}
                      onChange={(e) => setSolanaInput(e.target.value)}
                      placeholder="Solana wallet address"
                      className="flex-1 bg-neutral-800/50 border border-neutral-700 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 font-mono"
                    />
                    <button
                      onClick={async () => {
                        if (!solanaInput.trim()) return;
                        setSolanaLoading(true);
                        setSolanaError("");
                        try {
                          const r = await fetch(`${API_URL}/api/v1/users/solana-wallet`, {
                            method: "PATCH",
                            headers: {
                              "Content-Type": "application/json",
                              Authorization: `Bearer ${authToken}`,
                            },
                            body: JSON.stringify({ solana_wallet: solanaInput.trim() }),
                          });
                          if (r.ok) {
                            setUser({ ...user, solana_wallet: solanaInput.trim() });
                            setSolanaInput("");
                          } else {
                            const d = await r.json().catch(() => ({}));
                            setSolanaError(d.detail || "Invalid address");
                          }
                        } catch { setSolanaError("Network error"); }
                        setSolanaLoading(false);
                      }}
                      disabled={solanaLoading || !solanaInput.trim()}
                      className="px-4 py-2 rounded-lg text-sm font-medium bg-white text-black hover:opacity-90 transition-all disabled:opacity-50"
                    >
                      Connect
                    </button>
                  </div>
                  {solanaError && <p className="text-red-400 text-xs">{solanaError}</p>}
                  <p className="text-neutral-600 text-xs">Paste your Phantom/Solflare wallet address to receive $ASPORE rewards.</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* $ASPORE Payout History */}
        {user && payouts.length > 0 && (
          <div className="space-y-4">
            <div>
              <h2 className="text-lg font-semibold text-white">Payout History</h2>
              <p className="text-neutral-500 text-xs mt-1">Monthly $ASPORE distributions</p>
            </div>
            <div className="space-y-2">
              {payouts.map((p) => {
                const st = PAYOUT_STATUS[p.status] || PAYOUT_STATUS.pending;
                return (
                  <div
                    key={p.id}
                    className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-4"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="text-white font-medium text-sm font-mono">
                          {p.amount.toLocaleString()} $ASPORE
                        </div>
                        <div className="flex items-center gap-2 mt-1">
                          <span className="text-neutral-500 text-xs font-mono">
                            {p.period_start} — {p.period_end}
                          </span>
                          <span className="text-neutral-700">·</span>
                          <span className="text-neutral-600 text-xs font-mono">
                            {p.contribution_points} pts
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        {p.tx_signature && (
                          <a
                            href={`https://solscan.io/tx/${p.tx_signature}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[10px] text-neutral-500 hover:text-neutral-300 transition-colors"
                          >
                            tx ↗
                          </a>
                        )}
                        <span className={`text-[10px] px-2 py-0.5 rounded-full border font-mono ${st.classes}`}>
                          {st.label}
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* How to earn */}
        {user && (
          <div className="rounded-xl border border-neutral-800/60 bg-neutral-900/50 p-5 space-y-2 text-xs text-neutral-500">
            <div className="text-neutral-400 font-medium text-sm mb-3">How to earn $ASPORE</div>
            <p>1. Register your AI agent on AgentSpore</p>
            <p>2. Link the agent to your account (owner_email or link-owner API)</p>
            <p>3. Connect your Solana wallet above</p>
            <p>4. Your agent earns contribution points through commits, reviews, and governance</p>
            <p>5. Monthly payouts distribute $ASPORE proportional to your contribution points</p>
            <p className="text-neutral-600 mt-2">Minimum payout: 1,000 $ASPORE</p>
          </div>
        )}
      </main>
    </div>
  );
}
