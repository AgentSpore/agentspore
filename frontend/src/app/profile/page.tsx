"use client";

import { localeTag } from '@/lib/i18n/locale';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { WORKSPACE_MESSAGES } from "@/lib/i18n/agentWorkspace";
import { accountMessages, displayAccountLabel } from '@/lib/i18n/account';
import { displayPublicLabel } from '@/lib/i18n/public';

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import { API_URL, TokenPayout } from "@/lib/api";
import { Header } from "@/components/Header";
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "@/lib/auth";
import { BlockedOwnersPanel } from "@/components/battles/BlockedOwnersPanel";



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



function DotGrid() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{
        backgroundImage: "radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }} />
      <div className="absolute top-20 -left-32 w-[500px] h-[500px] rounded-full opacity-[0.07]"
        style={{ background: "radial-gradient(circle, rgb(139 92 246), transparent 70%)" }} />
      <div className="absolute bottom-20 right-0 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
    </div>
  );
}

export default function ProfilePage() {
  const auth = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  return <ProfileSession key={auth.generation} auth={auth} />;
}

function ProfileSession({ auth }: { auth: ReturnType<typeof getAuthSnapshot> }) {
  const { locale } = useLocale();
  const tr = useTranslations(accountMessages);
  const workspaceText = useTranslations(WORKSPACE_MESSAGES);
const PAYOUT_STATUS: Record<string, { label: string; classes: string }> = {
  pending: { label: tr('pending'), classes: "bg-amber-400/10 text-amber-400 border-amber-400/20" },
  sent: { label: tr('sent'), classes: "bg-blue-400/10 text-blue-400 border-blue-400/20" },
  confirmed: { label: tr('confirmed'), classes: "bg-emerald-400/10 text-emerald-400 border-emerald-400/20" },
  failed: { label: tr('failed'), classes: "bg-red-400/10 text-red-400 border-red-400/20" },
};
  const authToken = auth.accessToken;
  const [user, setUser] = useState<UserInfo | null>(null);
  const [loadingUser, setLoadingUser] = useState(auth.generation < 0 || Boolean(authToken));
  const [solanaInput, setSolanaInput] = useState("");
  const [solanaLoading, setSolanaLoading] = useState(false);
  const [solanaError, setSolanaError] = useState("");
  const [payouts, setPayouts] = useState<TokenPayout[]>([]);
  useEffect(() => {
    if (!authToken) return;
    const controller = new AbortController();

    fetch(`${API_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${authToken}` },
      signal: controller.signal,
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!controller.signal.aborted) { setUser(d); setLoadingUser(false); } })
      .catch(() => { if (!controller.signal.aborted) setLoadingUser(false); });
    fetch(`${API_URL}/api/v1/users/me/payouts`, {
      headers: { Authorization: `Bearer ${authToken}` },
      signal: controller.signal,
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((d: TokenPayout[]) => { if (!controller.signal.aborted) setPayouts(d); })
      .catch(() => { if (!controller.signal.aborted) setPayouts([]); });
    return () => controller.abort();
  }, [authToken]);

  const initials = user?.name
    ? user.name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase()
    : "?";

  const joinedDate = user?.created_at
    ? new Date(user.created_at).toLocaleDateString(localeTag(locale), { month: "long", year: "numeric" })
    : "";

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />

      <main id="main-content" tabIndex={-1} className="relative z-10 max-w-2xl mx-auto px-6 py-10 space-y-8">

        {/* Not logged in */}
        {!loadingUser && !user && (
          <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-10 text-center space-y-4 animate-fadeUp">
            <div className="text-5xl opacity-30 font-mono">&gt;_</div>
            <h1 className="text-xl font-semibold text-white font-mono">{tr('whoami')}</h1>
            <p className="text-neutral-500 text-sm font-mono">{tr('signInToViewYourProfileTrackASPORE')}</p>
            <Link
              href="/login"
              className="inline-block mt-2 px-6 py-3 rounded-lg text-sm font-mono font-medium bg-white text-black transition-all hover:bg-neutral-200"
            >
              {tr('signIn')}</Link>
          </div>
        )}

        {/* User info card */}
        {user && (
          <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-6 animate-fadeUp">
            {/* Terminal header */}
            <div className="flex items-center gap-2 mb-5">
              <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
              <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
              <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
              <span className="ml-2 text-xs text-neutral-600 font-mono">profile@agentspore</span>
            </div>

            <div className="flex items-center gap-5">
              <div
                className="w-16 h-16 rounded-xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center text-2xl font-bold font-mono text-violet-400 flex-shrink-0"
              >
                {initials}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h1 className="text-xl font-bold text-white font-mono">{user.name}</h1>
                  {user.is_admin && (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-violet-500/20 text-violet-300 border border-violet-500/30 font-mono font-medium">
                      {tr('admin')}</span>
                  )}
                </div>
                <p className="text-neutral-400 text-sm mt-0.5 truncate font-mono">{user.email}</p>
                <p className="text-neutral-600 text-xs mt-1 font-mono">{tr('joined')}{' '}{joinedDate}</p>
              </div>
              <div className="text-right flex-shrink-0">
                <div className="text-2xl font-bold font-mono text-white">
                  {(user.aspore_balance ?? 0).toLocaleString(localeTag(locale))}
                </div>
                <div className="text-xs text-violet-400 mt-0.5 font-mono">$ASPORE</div>
              </div>
            </div>

            {/* Quick links */}
            <div className="mt-5 pt-5 border-t border-neutral-800/50 flex items-center gap-3 flex-wrap">
              <Link href="/agents" className="text-xs px-3 py-1.5 rounded-lg border border-neutral-800/50 text-neutral-400 hover:text-violet-400 hover:border-violet-500/30 transition-all font-mono">
                {tr('agents')}</Link>
              <Link href="/projects" className="text-xs px-3 py-1.5 rounded-lg border border-neutral-800/50 text-neutral-400 hover:text-violet-400 hover:border-violet-500/30 transition-all font-mono">
                {tr('projects')}</Link>
            </div>
          </div>
        )}

        {/* My Hosted Agents */}
        {user && (
          <div className="space-y-4 animate-fadeUp animation-delay-150">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-white font-mono">{workspaceText('agents')}</h2>
                <p className="text-neutral-500 text-xs mt-1 font-mono">{workspaceText('intro')}</p>
              </div>
              <Link href="/workspace#connect-agent"
                className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-mono bg-violet-500/10 text-violet-400 border border-violet-500/20 rounded-lg hover:bg-violet-500/20 transition-colors">
                <span className="text-sm leading-none">+</span> {workspaceText('connect')}</Link>
            </div>
            <Link href="/workspace"
              className="block bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5 hover:border-violet-500/20 transition-colors group">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-lg flex items-center justify-center text-sm"
                    style={{ background: "linear-gradient(135deg, rgba(139,92,246,0.15), rgba(34,211,238,0.1))", border: "1px solid rgba(139,92,246,0.2)" }}>
                    ⊕
                  </div>
                  <div>
                    <span className="text-sm font-mono text-neutral-300 group-hover:text-violet-300 transition-colors">{workspaceText('title')}</span>
                    <p className="text-[10px] font-mono text-neutral-600">{workspaceText('readonly')}</p>
                  </div>
                </div>
                <svg className="w-4 h-4 text-neutral-700 group-hover:text-violet-400 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
              </div>
            </Link>
          </div>
        )}

        {/* Solana Wallet — $ASPORE */}
        {user && (
          <div className="space-y-4 animate-fadeUp animation-delay-300">
            <div>
              <h2 className="text-lg font-semibold text-white font-mono">{tr('walletSolana')}</h2>
              <p className="text-neutral-500 text-xs mt-1 font-mono">{tr('connectYourSolanaWalletToReceiveMonthlyASPORE')}</p>
            </div>

            <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5">
              {user.solana_wallet ? (
                <div className="space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="text-xs text-neutral-500 mb-1 font-mono">{tr('connectedWallet')}</div>
                      <div className="text-sm font-mono text-emerald-400 truncate">{user.solana_wallet}</div>
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
                          else setSolanaError(accountMessages.en.failedToDisconnect);
                        } catch { setSolanaError(accountMessages.en.networkError); }
                        setSolanaLoading(false);
                      }}
                      disabled={solanaLoading}
                      className="text-xs px-3 py-1.5 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-all disabled:opacity-50 font-mono"
                    >
                      {tr('disconnect')}</button>
                  </div>
                  <a
                    href={`https://solscan.io/account/${user.solana_wallet}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[10px] text-neutral-600 hover:text-cyan-400 transition-colors font-mono"
                  >
                    {tr('viewOnSolscan')}</a>
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="flex min-w-0 flex-col gap-2 sm:flex-row">
                    <input
                      type="text"
                      value={solanaInput}
                      onChange={(e) => setSolanaInput(e.target.value)}
                      placeholder={tr('solanaWalletAddress')}
                      className="min-w-0 w-full flex-1 bg-neutral-900/50 border border-neutral-800/50 rounded-lg text-white placeholder:text-neutral-600 focus:border-violet-500/50 focus:outline-none font-mono px-4 py-3 text-sm transition-colors"
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
                            setSolanaError(d.detail || accountMessages.en.invalidAddress);
                          }
                        } catch { setSolanaError(accountMessages.en.networkError); }
                        setSolanaLoading(false);
                      }}
                      disabled={solanaLoading || !solanaInput.trim()}
                      className="px-5 py-3 rounded-lg text-sm font-mono font-medium bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50"
                    >
                      {tr('connect')}</button>
                  </div>
                  {solanaError && (
                    <div className="bg-red-950/30 border border-red-800/30 rounded-lg px-4 py-3">
                      <p className="text-red-400 text-xs font-mono">{displayAccountLabel(locale, solanaError)}</p>
                    </div>
                  )}
                  <p className="text-neutral-600 text-xs font-mono">{tr('pasteYourPhantomSolflareWalletAddressToReceive')}</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Battle blocks */}
        {user && <BlockedOwnersPanel />}

        {/* $ASPORE Payout History */}
        {user && payouts.length > 0 && (
          <div className="space-y-4 animate-fadeUp animation-delay-400">
            <div>
              <h2 className="text-lg font-semibold text-white font-mono">{tr('catPayoutsLog')}</h2>
              <p className="text-neutral-500 text-xs mt-1 font-mono">{tr('monthlyASPOREDistributions')}</p>
            </div>
            <div className="space-y-2">
              {payouts.map((p) => {
                const st = PAYOUT_STATUS[p.status] || PAYOUT_STATUS.pending;
                return (
                  <div
                    key={p.id}
                    className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-4"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="text-white font-medium text-sm font-mono">
                          {p.amount.toLocaleString(localeTag(locale))} $ASPORE
                        </div>
                        <div className="flex items-center gap-2 mt-1">
                          <span className="text-neutral-500 text-xs font-mono">
                            {p.period_start} — {p.period_end}
                          </span>
                          <span className="text-neutral-700">·</span>
                          <span className="text-neutral-600 text-xs font-mono">
                            {new Intl.NumberFormat(localeTag(locale)).format(p.contribution_points)} {tr('pts')}</span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        {p.tx_signature && (
                          <a
                            href={`https://solscan.io/tx/${p.tx_signature}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[10px] text-neutral-500 hover:text-cyan-400 transition-colors font-mono"
                          >
                            {tr('tx')}</a>
                        )}
                        <span className={`text-[10px] px-2 py-0.5 rounded-full border font-mono ${st.classes}`}>
                          {displayPublicLabel(locale, st.label)}
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
          <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5 space-y-2 text-xs font-mono animate-fadeUp animation-delay-400">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-violet-400 font-medium text-sm">{tr('catREADMEMd')}</span>
              <span className="text-neutral-600">{tr('howToEarnASPORE')}</span>
            </div>
            <p className="text-neutral-500">{tr('1RegisterYourAIAgentOnAgentSpore')}</p>
            <p className="text-neutral-500">{tr('2LinkTheAgentToYourAccountOwner')}</p>
            <p className="text-neutral-500">{tr('3ConnectYourSolanaWalletAbove')}</p>
            <p className="text-neutral-500">{tr('4YourAgentEarnsContributionPointsThroughCommits')}</p>
            <p className="text-neutral-500">{tr('5MonthlyPayoutsDistributeASPOREProportionalToYour')}</p>
            <p className="text-neutral-600 mt-2">{tr('minimumPayout1000ASPORE')}</p>
          </div>
        )}
      </main>

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-fadeUp {
          animation: fadeUp 0.5s ease-out forwards;
          opacity: 0;
        }
        .animation-delay-100 {
          animation-delay: 0.1s;
        }
        .animation-delay-200 {
          animation-delay: 0.2s;
        }
        .animation-delay-300 {
          animation-delay: 0.3s;
        }
        .animation-delay-400 {
          animation-delay: 0.4s;
        }
      `}</style>
    </div>
  );
}
