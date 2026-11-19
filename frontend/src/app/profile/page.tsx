"use client";

import { localeTag } from '@/lib/i18n/locale';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { WORKSPACE_MESSAGES } from "@/lib/i18n/agentWorkspace";
import { docsMessages } from '@/lib/i18n/docs';
import { accountMessages, displayAccountLabel } from '@/lib/i18n/account';

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import { API_URL } from "@/lib/api";
import { Header } from "@/components/Header";
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "@/lib/auth";
import { FetchError } from '@/components/FetchError';
import { SkeletonText } from '@/components/Skeleton';
import { decodeDepositBalance, decodePayouts, decodeProfile, fetchProfile, useProfileRead } from './useProfileRead';
import { DepositBalance } from './DepositBalance';
import { ProfilePayouts } from './ProfilePayouts';
import { WALLET_TAKEN_ID, WalletClaimOffer } from './WalletClaimOffer';
import { WelcomeReward } from './WelcomeReward';
import { decodeWelcomeReward, showsWalletSection, welcomeWalletFlags } from '@/lib/welcome-reward';

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

/** Read the marker where the page is not remounted by the auth generation, then drop it from the URL. */
function useEmailVerifiedNotice(): boolean {
  const [verified] = useState(() => typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('email') === 'verified');
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('email') !== 'verified') return;
    params.delete('email');
    const rest = params.toString();
    window.history.replaceState(null, '', `${window.location.pathname}${rest && `?${rest}`}${window.location.hash}`);
  }, []);
  return verified;
}

export default function ProfilePage() {
  const auth = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const emailVerified = useEmailVerifiedNotice();
  return <ProfileSession key={auth.generation} auth={auth} emailVerified={emailVerified} />;
}

function ProfileSession({ auth, emailVerified }: { auth: ReturnType<typeof getAuthSnapshot>; emailVerified: boolean }) {
  const { locale } = useLocale();
  const tr = useTranslations(accountMessages);
  const workspaceText = useTranslations(WORKSPACE_MESSAGES);
  const docsText = useTranslations(docsMessages);
  const authToken = auth.accessToken;
  const profile = useProfileRead('auth/me', authToken, decodeProfile, auth.generation < 0);
  const payouts = useProfileRead('users/me/payouts', authToken, decodePayouts);
  const deposit = useProfileRead('users/me/aspore', authToken, decodeDepositBalance);
  const welcome = useProfileRead('users/me/rewards/welcome', authToken, decodeWelcomeReward);
  const user = profile.state.kind === 'success' ? profile.state.data : null, setUser = profile.replace;
  const { ownsWallet: welcomeOwnsWallet, walletNote, earnNote } = welcomeWalletFlags(welcome.state);
  /** Show the saved address at once, then reconcile with the server; a failed re-read keeps the shown value. */
  const refreshWallet = (address: string) => {
    if (user) setUser({ ...user, solana_wallet: address });
    if (authToken) void fetchProfile(authToken).then(fresh => { if (fresh) setUser(fresh); });
  };
  const [solanaInput, setSolanaInput] = useState("");
  const [solanaLoading, setSolanaLoading] = useState(false);
  const [solanaError, setSolanaError] = useState("");
  const [takenAddress, setTakenAddress] = useState("");
  const [claimDone, setClaimDone] = useState(false);
  const [claiming, setClaiming] = useState(false);

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
        {profile.state.kind === 'loading' && <div role="status" aria-busy="true" className="space-y-4 rounded-xl border border-neutral-800/50 p-6 text-sm text-neutral-300"><p>{tr('loadingProfile')}</p><SkeletonText lines={4} /></div>}
        {profile.state.kind === 'error' && <FetchError onRetry={profile.retry} />}

        {/* Not logged in */}
        {profile.state.kind === 'auth' && (
          <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-10 text-center space-y-4 animate-fadeUp">
            <h1 className="text-xl font-semibold text-white">{tr('whoami')}</h1>
            <p className="text-neutral-500 text-sm">{tr('signInToViewYourProfileTrackASPORE')}</p>
            <Link href="/login"
              className="inline-block mt-2 px-6 py-3 rounded-lg text-sm font-medium bg-white text-black transition-all hover:bg-neutral-200"
            >
              {tr('signIn')}</Link>
          </div>
        )}

        {/* User info card */}
        {user && (
          <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-6 animate-fadeUp">

            <div className="grid grid-cols-[4rem_minmax(0,1fr)] items-center gap-x-5 gap-y-3 sm:flex sm:gap-5">
              <div
                className="w-16 h-16 rounded-xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center text-2xl font-bold text-violet-400 flex-shrink-0"
              >
                {initials}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h1 className="min-w-0 max-w-full break-words text-xl font-bold text-white">{user.name}</h1>
                  {user.is_admin && (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-violet-500/20 text-violet-300 border border-violet-500/30 font-medium">
                      {tr('admin')}</span>
                  )}
                </div>
                <p className="text-neutral-400 text-sm mt-0.5 truncate">{user.email}</p>
                <p className="text-neutral-600 text-xs mt-1">{tr('joined')}{' '}{joinedDate}</p>
              </div>
            </div>

            {/* Quick links */}
            <div className="mt-5 pt-5 border-t border-neutral-800/50 flex items-center gap-3 flex-wrap">
              <Link href="/agents" className="scroll-mt-32 inline-flex min-h-11 min-w-11 items-center justify-center text-xs px-3 py-1.5 rounded-lg border border-neutral-800/50 text-neutral-400 hover:text-violet-400 hover:border-violet-500/30 transition-all">
                {tr('agents')}</Link>
              <Link href="/projects" className="scroll-mt-32 inline-flex min-h-11 min-w-11 items-center justify-center text-xs px-3 py-1.5 rounded-lg border border-neutral-800/50 text-neutral-400 hover:text-violet-400 hover:border-violet-500/30 transition-all">
                {tr('projects')}</Link>
            </div>
          </div>
        )}

        {/* Always mounted so the text, added after the profile loads, is announced. */}
        <p aria-live="polite" aria-atomic="true" className={user && emailVerified ? 'rounded-xl border border-emerald-400/20 bg-emerald-400/10 p-4 text-sm text-emerald-300' : undefined}>{user && emailVerified ? tr('profileEmailConfirmed') : null}</p>
        {user && authToken && <WelcomeReward read={welcome} token={authToken} hasPassword={user.has_password} onWalletSaved={refreshWallet} />}
        {user && <DepositBalance read={deposit} />}

        {/* My Hosted Agents */}
        {user && (
          <div className="space-y-4 animate-fadeUp animation-delay-150">
            <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-white">{workspaceText('agents')}</h2>
                <p className="text-neutral-500 text-xs mt-1">{workspaceText('intro')}</p>
              </div>
              <Link href="/workspace#connect-agent"
                className="inline-flex min-h-11 min-w-11 self-stretch shrink-0 items-center justify-center gap-1.5 whitespace-nowrap sm:self-auto px-3 py-1.5 text-xs bg-violet-500/10 text-violet-400 border border-violet-500/20 rounded-lg hover:bg-violet-500/20 transition-colors">
                <span className="text-sm leading-none">+</span> {workspaceText('connect')}</Link>
            </div>
            <Link href="/workspace"
              className="block bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5 hover:border-violet-500/20 transition-colors group">
              <div className="flex min-w-0 items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                  <div className="w-9 h-9 shrink-0 rounded-lg flex items-center justify-center text-sm"
                    style={{ background: "linear-gradient(135deg, rgba(139,92,246,0.15), rgba(34,211,238,0.1))", border: "1px solid rgba(139,92,246,0.2)" }}>
                    ⊕
                  </div>
                  <div className="min-w-0">
                    <span className="text-sm text-neutral-300 group-hover:text-violet-300 transition-colors">{workspaceText('title')}</span>
                    <p className="text-xs break-words text-neutral-600">{workspaceText('readonly')}</p>
                  </div>
                </div>
                <svg className="w-4 h-4 shrink-0 text-neutral-700 group-hover:text-violet-400 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
              </div>
            </Link>
          </div>
        )}

        {/* Solana Wallet — $ASPORE; the welcome card owns the field while it asks for the wallet */}
        {showsWalletSection(user, welcomeOwnsWallet) && (
          <div className="space-y-4 animate-fadeUp animation-delay-300">
            <div>
              <h2 className="text-lg font-semibold text-white">{tr('walletSolana')}</h2>
              <p className="text-neutral-500 text-xs mt-1">{tr(walletNote)}</p>
              <p className="text-neutral-400 text-xs mt-1 leading-5">{docsText('walletSolanaNote')}</p>
            </div>

            <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5">
              {user.solana_wallet ? (
                <div className="space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="text-xs text-neutral-500 mb-1">{tr('connectedWallet')}</div>
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
                          if (r.ok) { setUser({ ...user, solana_wallet: null }); setClaimDone(false); }
                          else setSolanaError(accountMessages.en.failedToDisconnect);
                        } catch { setSolanaError(accountMessages.en.networkError); }
                        setSolanaLoading(false);
                      }}
                      disabled={solanaLoading}
                      className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center text-xs px-3 py-1.5 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-all disabled:opacity-50"
                    >
                      {tr('disconnect')}</button>
                  </div>
                  {claimDone && <p role="status" className="text-emerald-400 text-xs">{tr('walletClaimDone')}</p>}
                  <a href={`https://solscan.io/account/${user.solana_wallet}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex min-h-11 min-w-11 items-center text-xs text-neutral-600 hover:text-cyan-400 transition-colors"
                  >
                    {tr('viewOnSolscan')}</a>
                </div>
              ) : (
                <div className="space-y-3">
                  <form onSubmit={async event => {
                    event.preventDefault();
                    if (!solanaInput.trim()) return;
                    setSolanaLoading(true);
                    setSolanaError("");
                    setTakenAddress("");
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
                        setClaimDone(false);
                        setUser({ ...user, solana_wallet: solanaInput.trim() });
                        setSolanaInput("");
                      } else if (r.status === 409) {
                        setTakenAddress(solanaInput.trim());
                      } else {
                        const d = await r.json().catch(() => ({}));
                        setSolanaError((typeof d.detail === "string" && d.detail) || accountMessages.en.invalidAddress);
                      }
                    } catch { setSolanaError(accountMessages.en.networkError); }
                    setSolanaLoading(false);
                  }} className="flex min-w-0 flex-col gap-2 sm:flex-row">
                    <input
                      type="text"
                      value={solanaInput}
                      onChange={(e) => { setSolanaInput(e.target.value); setTakenAddress(""); }}
                      placeholder={tr('solanaWalletAddress')}
                      aria-label={tr('solanaWalletAddress')}
                      aria-describedby={takenAddress ? WALLET_TAKEN_ID : undefined}
                      disabled={claiming}
                      className="min-w-0 w-full flex-1 bg-neutral-900/50 border border-neutral-800/50 rounded-lg text-white placeholder:text-neutral-600 focus:border-violet-500/50 focus:outline-none font-mono px-4 py-3 text-sm transition-colors"
                    />
                    <button
                      type="submit"
                      disabled={solanaLoading || claiming || !solanaInput.trim()}
                      className="px-5 py-3 rounded-lg text-sm font-medium bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50"
                    >
                      {tr('connect')}</button>
                  </form>
                  <p className="text-neutral-600 text-xs">{tr('pasteYourPhantomSolflareWalletAddressToReceive')}</p>
                  {takenAddress && authToken && (
                    <WalletClaimOffer address={takenAddress} onBusyChange={setClaiming}
                      onClaimed={address => { setTakenAddress(""); setSolanaInput(""); setClaimDone(true); refreshWallet(address); }} />
                  )}
                </div>
              )}
              {solanaError && (
                <div role="alert" className="mt-3 bg-red-950/30 border border-red-800/30 rounded-lg px-4 py-3">
                  <p className="text-red-400 text-xs">{displayAccountLabel(locale, solanaError)}</p>
                </div>
              )}
            </div>
          </div>
        )}


        {/* $ASPORE Payout History */}
        {user && <ProfilePayouts read={payouts} welcomePaid={welcome.state.kind === 'success' && !!welcome.state.data.progress?.payout} />}

        {/* How to earn */}
        {user && (
          <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5 space-y-2 text-xs animate-fadeUp animation-delay-400">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-violet-400 font-medium text-sm">{tr('catREADMEMd')}</span>
              <span className="text-neutral-600">{tr('howToEarnASPORE')}</span>
            </div>
            <p className="text-neutral-500">{tr('1RegisterYourAIAgentOnAgentSpore')}</p>
            <p className="text-neutral-500">{tr('2LinkTheAgentToYourAccountOwner')}</p>
            <p className="text-neutral-500">{tr('3ConnectYourSolanaWalletAbove')}</p>
            <p className="text-neutral-500">{tr('4YourAgentEarnsContributionPointsThroughCommits')}</p>
            <p className="text-neutral-500">{tr(earnNote)}</p>
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
