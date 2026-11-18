"use client";

import { LanguageSelector } from "@/components/LanguageSelector";

import { localeTag } from '@/lib/i18n/locale';

import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { accountMessages, displayAccountLabel } from '@/lib/i18n/account';

import Link from "next/link";
import { Suspense, useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { API_URL } from "@/lib/api";
import { AUTH_FLOW_MESSAGES } from '@/lib/i18n/authFlow';
import { resendVerification } from "../login/resendVerification";
import { LinkProblem } from "./LinkProblem";

type State =
  | { status: "verifying" }
  | { status: "success" }
  | { status: "error"; message: string; expired: boolean; temporary?: boolean }
  | { status: "missing" }
  | { status: "resent" };

function DotGrid() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{
        backgroundImage: "radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }} />
      <div className="absolute top-20 -left-32 w-[500px] h-[500px] rounded-full opacity-[0.07]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
      <div className="absolute bottom-20 right-0 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(16 185 129), transparent 70%)" }} />
    </div>
  );
}

export default function VerifyEmailPage() {
  const tr = useTranslations(accountMessages);
  return (
    <main id="main-content" tabIndex={-1}>
      <Suspense fallback={
        <div className="relative min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center">
          <div className="absolute right-4 top-4"><LanguageSelector /></div>
          <div className="text-neutral-500 text-sm font-mono animate-pulse">{tr('initializing')}</div>
        </div>
      }>
        <VerifyEmailInner />
      </Suspense>
    </main>
  );
}

function VerifyEmailInner() {
  const { locale } = useLocale();
  const tr = useTranslations(accountMessages);
  const flowTr = useTranslations(AUTH_FLOW_MESSAGES);
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [state, setState] = useState<State>(
    token ? { status: "verifying" } : { status: "missing" }
  );
  const [resendEmail, setResendEmail] = useState("");
  const [resendLoading, setResendLoading] = useState(false);
  const [redirectCountdown, setRedirectCountdown] = useState(2);
  const [attempt, setAttempt] = useState(0);
  const lastAttempt = useRef(-1);

  // Verify once per attempt; the ref keeps a repeated effect run from sending the same attempt twice
  useEffect(() => {
    if (!token || lastAttempt.current === attempt) return;
    lastAttempt.current = attempt;

    void (async () => {
      try {
        const res = await fetch(
          `${API_URL}/api/v1/auth/verify-email?token=${encodeURIComponent(token)}`,
          { method: "GET" }
        );
        const body = await res.json().catch(() => ({})) as Record<string, unknown>;

        if (res.ok) {
          const access = body.access_token as string | undefined;
          const refresh = body.refresh_token as string | undefined;
          if (access) {
            try {
              localStorage.setItem("access_token", access);
              if (refresh) localStorage.setItem("refresh_token", refresh);
            } catch {
              // private mode — proceed anyway
            }
          }
          setState({ status: "success" });
        } else {
          // Any answer other than success means the link could not be used; the page offers a new one.
          const message = typeof body.detail === "string" ? body.detail : accountMessages.en.verificationFailedTheLinkMayBeInvalid;
          setState({ status: "error", message, expired: true, temporary: res.status >= 500 || res.status === 429 });
        }
      } catch {
        setState({
          status: "error",
          message: accountMessages.en.networkErrorCouldNotReachTheServer,
          expired: false,
        });
      }
    })();
  }, [token, attempt]);

  // Auto-redirect after success
  useEffect(() => {
    if (state.status !== "success") return;
    if (redirectCountdown <= 0) {
      window.location.href = new URL("/profile?email=verified", window.location.origin).href;
      return;
    }
    const t = setTimeout(() => setRedirectCountdown(c => c - 1), 1000);
    return () => clearTimeout(t);
  }, [state.status, redirectCountdown]);

  const handleResend = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!resendEmail.trim()) return;
    setResendLoading(true);
    await resendVerification(resendEmail);
    setResendLoading(false);
    setState({ status: "resent" });
  };

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center px-4 relative">
      <DotGrid />

      <div className="relative w-full max-w-md z-10">
        <div className="mb-4 flex justify-end"><LanguageSelector /></div>
        {/* Logo */}
        <div className="text-center mb-10 animate-fadeUp">
          <Link href="/" className="inline-flex min-h-11 min-w-11 items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-neutral-900/30 border border-neutral-800/50 backdrop-blur-sm flex items-center justify-center text-lg text-cyan-400">
              &gt;_
            </div>
            <span className="text-xl font-bold font-mono">AgentSpore</span>
          </Link>
        </div>

        <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-8 animate-fadeUp animation-delay-100">
          {state.status === "verifying" && (
            <div className="text-center py-6" data-testid="state-verifying">
              <div className="inline-block w-6 h-6 border-2 border-neutral-700 border-t-cyan-400 rounded-full animate-spin mb-4" />
              <p className="text-neutral-400 text-sm font-mono">{tr('verifyingYourEmail')}</p>
            </div>
          )}

          {state.status === "success" && (
            <div className="text-center py-4" data-testid="state-success">
              <div className="text-3xl mb-4 text-emerald-400 font-mono">{tr('oK')}</div>
              <h2 className="text-white font-mono font-semibold text-lg mb-2">{tr('emailVerified')}</h2>
              <p className="text-neutral-400 text-sm font-mono mb-6">
                {tr('youReSignedInRedirectingToYourProfile')}{" "}{new Intl.NumberFormat(localeTag(locale)).format(redirectCountdown)}{" "}{tr('s')}</p>
              <Link href="/profile?email=verified"
                className="inline-flex min-h-11 min-w-11 items-center justify-center px-6 py-2.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-sm font-mono hover:bg-emerald-500/20 transition-colors"
              >
                {tr('goToProfile')}</Link>
            </div>
          )}

          {state.status === "error" && state.expired && (
            <LinkProblem reason={state.temporary ? "unavailable" : "expired"} onRetry={() => { setState({ status: "verifying" }); setAttempt(n => n + 1); }} email={resendEmail} onEmailChange={setResendEmail} loading={resendLoading} onSubmit={handleResend} />
          )}

          {state.status === "error" && !state.expired && (
            <div data-testid="state-error">
              <div role="alert" className="bg-red-950/30 border border-red-800/30 rounded-lg px-4 py-3 mb-6">
                <p className="text-red-400 text-xs font-mono" data-testid="error-message">
                  {displayAccountLabel(locale, state.message)}
                </p>
              </div>
              <p className="text-center mt-4">
                <Link href="/login" className="inline-flex min-h-11 min-w-11 items-center justify-center text-xs text-neutral-500 hover:text-cyan-400 transition-colors font-mono">
                  {tr('backToLogin')}</Link>
              </p>
            </div>
          )}

          {state.status === "missing" && (
            <LinkProblem reason="missing" email={resendEmail} onEmailChange={setResendEmail} loading={resendLoading} onSubmit={handleResend} />
          )}

          {state.status === "resent" && (
            <div className="text-center py-4" data-testid="state-resent">
              <div className="text-3xl mb-4 text-cyan-400 font-mono">{tr('sENT')}</div>
              <h2 className="text-white font-mono font-semibold text-lg mb-2">{tr('checkYourInbox')}</h2>
              <p className="text-neutral-400 text-sm font-mono mb-6">
                {tr('ifTheAccountExistsAndIsUnverifiedA')}</p>
              <Link href="/login" className="inline-flex min-h-11 min-w-11 items-center justify-center text-xs text-neutral-500 hover:text-cyan-400 transition-colors font-mono">
                {tr('backToLogin')}</Link>
            </div>
          )}
        </div>

        <p className="text-center text-xs text-neutral-600 mt-6 font-mono animate-fadeUp animation-delay-200">
          <Link href="/" className="inline-flex min-h-11 min-w-11 items-center justify-center hover:text-cyan-400 transition-colors">{flowTr('backToHome')}</Link>
        </p>
      </div>

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-fadeUp {
          animation: fadeUp 0.5s ease-out forwards;
          opacity: 0;
        }
        .animation-delay-100 { animation-delay: 0.1s; }
        .animation-delay-200 { animation-delay: 0.2s; }
      `}</style>
    </div>
  );
}
