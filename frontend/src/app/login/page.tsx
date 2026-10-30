"use client";

import { WORKSPACE_MESSAGES } from "@/lib/i18n/agentWorkspace";

import { Header } from "@/components/Header";

import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { accountMessages, displayAccountLabel } from '@/lib/i18n/account';

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { API_URL } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { useOAuthProviders } from "@/hooks/useOAuthProviders";

import { rememberClaimReturn } from "@/lib/agent-claim";

const AUTH_TIMEOUT_MS = 15000;

const ALLOWED_NEXT_PREFIXES = ["/dashboard", "/profile", "/agents", "/projects", "/chat", "/workspace"];
function safeNext(raw: string | null): string {
  if (!raw) return "/profile";
  if (!raw.startsWith("/")) return "/profile";
  if (raw.startsWith("//")) return "/profile";
  if (!ALLOWED_NEXT_PREFIXES.some(p => raw === p || raw.startsWith(p + "/") || raw.startsWith(p + "?"))) return "/profile";
  return raw;
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

export default function LoginPage() {
  return (
    <Suspense fallback={<main id="main-content" tabIndex={-1} aria-busy="true" />}>
      <LoginPageInner />
    </Suspense>
  );
}

function LoginPageInner() {
  const { locale } = useLocale();
  const tr = useTranslations(accountMessages);
  const workspaceTr = useTranslations(WORKSPACE_MESSAGES);
  const searchParams = useSearchParams();
  const nextUrl = safeNext(searchParams.get("next"));
  const { info: toastInfo } = useToast();
  const [tab, setTab] = useState<"login" | "register">(() => searchParams.get("tab") === "register" ? "register" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [slowHint, setSlowHint] = useState(false);
  const oauth = useOAuthProviders();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSlowHint(false);
    setLoading(true);

    const action = tab === "login" ? "login" : "register";
    const url = tab === "login" ? `${API_URL}/api/v1/auth/login` : `${API_URL}/api/v1/auth/register`;
    const body = tab === "login" ? { email, password } : { email, password, name };

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), AUTH_TIMEOUT_MS);
    const slowHintId = setTimeout(() => setSlowHint(true), 3000);

    console.log(`[auth:${action}] submit →`, url);

    let res: Response;
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      clearTimeout(timeoutId);
      clearTimeout(slowHintId);
      setSlowHint(false);
      setLoading(false);
      const isAbort = err instanceof DOMException && err.name === "AbortError";
      const isNetwork = err instanceof TypeError;
      console.error(`[auth:${action}] fetch failed`, { url, error: err });
      if (isAbort) {
        setError(tr('serverDidNotRespondInSecondsSCheck', { seconds: AUTH_TIMEOUT_MS / 1000 }));
      } else if (isNetwork) {
        setError(accountMessages.en.networkErrorCannotReachServerCheckCORSAd);
      } else {
        setError(err instanceof Error ? err.message : accountMessages.en.failedToConnectToServer);
      }
      return;
    }
    clearTimeout(timeoutId);
    clearTimeout(slowHintId);
    setSlowHint(false);

    console.log(`[auth:${action}] response`, { status: res.status, ok: res.ok });

    if (!res.ok) {
      let detail = tr('authenticationFailedHTTPStatus', { status: res.status });
      try {
        const errBody = await res.json();
        if (errBody?.detail) detail = typeof errBody.detail === "string" ? errBody.detail : JSON.stringify(errBody.detail);
      } catch (parseErr) {
        console.error(`[auth:${action}] error body parse failed`, parseErr);
      }
      console.error(`[auth:${action}] server rejected`, { status: res.status, detail });
      setLoading(false);
      setError(detail);
      return;
    }

    let data: { access_token?: string; refresh_token?: string; requires_verification?: boolean; message?: string };
    try {
      data = await res.json();
    } catch (parseErr) {
      console.error(`[auth:${action}] success body parse failed`, parseErr);
      setLoading(false);
      setError(accountMessages.en.serverReturnedAnInvalidResponsePleaseTryAgain);
      return;
    }

    // Registration returns 202 + requires_verification=true — no JWT yet.
    // User must click the link in their email before they can log in.
    if (action === "register" && data?.requires_verification) {
      setLoading(false);
      toastInfo(tr('checkYourEmailToVerifyYourAccountThen'));
      setTab("login");
      setPasswordVisible(false);
      setPassword("");
      return;
    }

    if (!data?.access_token) {
      console.error(`[auth:${action}] missing access_token`, data);
      setLoading(false);
      setError(accountMessages.en.serverReturnedAnIncompleteResponsePleaseTryAgain);
      return;
    }

    try {
      localStorage.setItem("access_token", data.access_token);
      if (data.refresh_token) localStorage.setItem("refresh_token", data.refresh_token);
    } catch (storeErr) {
      console.error(`[auth:${action}] localStorage failed`, storeErr);
      setLoading(false);
      setError(accountMessages.en.couldNotSaveSessionCheckBrowserStorageSettings);
      return;
    }

    const dest = nextUrl;
    console.log(`[auth:${action}] success → ${dest}`);
    toastInfo(tr('signedInRedirecting'));
    window.location.href = dest;
  };

  const inputClass =
    "w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg text-white placeholder:text-neutral-400 focus:border-violet-500/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400 px-4 py-3 text-sm transition-colors";

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      {/* Logo */}
      <Header />
      <main id="main-content" tabIndex={-1} className="relative flex justify-center px-4 py-10 pb-40 sm:py-16 sm:pb-40">
      <DotGrid />

      <div className="relative w-full max-w-md z-10">
        <div className="mb-8 space-y-3">
          <h1 className="text-3xl font-semibold tracking-tight">{tab === 'login' ? tr('signIn') : tr('createAccount')}</h1>
          <p className="text-sm leading-6 text-neutral-300">{tr('accountAccessBenefit')}</p>
          <p className="text-sm leading-6 text-neutral-400">{tr('servicesWithoutAccount')}</p>
          <p className="text-xs leading-5 text-neutral-400">{workspaceTr('homeIntro')}</p>
        </div>

        <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5 sm:p-8">
          {/* Terminal header */}
          {/* Tabs */}
          <div className="flex rounded-lg overflow-hidden border border-neutral-800/50 mb-6 text-sm">
            {(["login", "register"] as const).map(t => (
              <button key={t} type="button" aria-pressed={tab === t} onClick={() => { setTab(t); setPasswordVisible(false); }}
                className={`min-h-11 flex-1 py-2.5 font-medium transition-colors ${
                  tab === t
                    ? "bg-violet-500/10 text-violet-400 border-b-2 border-violet-400"
                    : "text-neutral-400 hover:text-neutral-300"
                }`}>
                {t === 'login' ? tr('signIn2') : tr('register')}
              </button>
            ))}
          </div>

          {/* Email form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            {tab === "register" && (
              <div>
                <label htmlFor="auth-name" className="block text-xs text-neutral-400 mb-1.5">{tr('name')}</label>
                <input id="auth-name" value={name} onChange={e => setName(e.target.value)}
                  placeholder={tr('yourName')} autoComplete="name" required
                  className={inputClass} />
              </div>
            )}
            <div>
              <label htmlFor="auth-email" className="block text-xs text-neutral-400 mb-1.5">{tr('email')}</label>
              <input id="auth-email" value={email} onChange={e => setEmail(e.target.value)}
                type="email" placeholder="you@example.com" autoComplete="email" required
                className={inputClass} />
            </div>
            <div>
              <label htmlFor="auth-password" className="block text-xs text-neutral-400 mb-1.5">{tr('password')}</label>
              <div className="relative">
                <input id="auth-password" value={password} onChange={e => setPassword(e.target.value)}
                  type={passwordVisible ? "text" : "password"} placeholder={tr('enterYourPassword')} required
                  autoComplete={tab === "login" ? "current-password" : "new-password"}
                  className={`${inputClass} pr-24`} />
                <button type="button" aria-controls="auth-password" aria-pressed={passwordVisible}
                  aria-label={passwordVisible ? tr('hidePassword') : tr('showPassword')}
                  onMouseDown={e => e.preventDefault()} onClick={() => setPasswordVisible(value => !value)}
                  className="absolute inset-y-0 right-1 min-h-11 min-w-11 rounded-md px-3 text-xs text-violet-300 hover:text-violet-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300">
                  {passwordVisible ? tr('hide') : tr('show')}
                </button>
              </div>
            </div>

            {tab === "login" && (
              <div className="text-right">
                <Link href="/forgot-password" className="inline-flex min-h-11 items-center px-1 text-xs text-neutral-400 hover:text-violet-400 transition-colors">
                  {tr('forgotPassword')}</Link>
              </div>
            )}

            {error && (
              <div className="bg-red-950/30 border border-red-800/30 rounded-lg px-4 py-3">
                <p role="alert" className="text-red-400 text-xs">{displayAccountLabel(locale, error)}</p>
              </div>
            )}

            {loading && slowHint && (
              <div className="bg-amber-950/20 border border-amber-800/30 rounded-lg px-4 py-3">
                <p className="text-amber-400/90 text-xs">
                  {tr('serverIsTakingLongerThanUsualHangTight')}</p>
              </div>
            )}

            <button type="submit" disabled={loading}
              className="w-full py-3 rounded-lg text-sm font-medium bg-white text-black transition-all hover:bg-neutral-200 disabled:opacity-50 flex items-center justify-center gap-2">
              {loading && (
                <span className="inline-block w-3.5 h-3.5 border-2 border-black/30 border-t-black rounded-full animate-spin" />
              )}
              {loading
                ? (tab === "login" ? tr('signingIn') : tr('creatingAccount'))
                : (tab === "login" ? tr('signIn') : tr('createAccount'))}
            </button>
          </form>

          {(oauth.github || oauth.google) && (
            <>
              <div className="flex items-center gap-3 my-6">
                <div className="flex-1 h-px bg-neutral-800/50" />
                <span className="text-xs text-neutral-400">{tr('orSignInWith')}</span>
                <div className="flex-1 h-px bg-neutral-800/50" />
              </div>
              <div className="space-y-2.5">
                {oauth.github && (
                  <a onClick={() => rememberClaimReturn(nextUrl)} href={`${API_URL}/api/v1/oauth/github`}
                    className="flex scroll-mb-40 items-center justify-center gap-3 w-full py-3 rounded-lg border border-neutral-800/50 bg-neutral-900/50 hover:bg-neutral-800/50 hover:border-neutral-700/50 transition-all text-sm text-neutral-300">
                    <GithubIcon /> {tr('continueWithGitHub')}</a>
                )}
                {oauth.google && (
                  <a onClick={() => rememberClaimReturn(nextUrl)} href={`${API_URL}/api/v1/oauth/google`}
                    className="flex scroll-mb-40 items-center justify-center gap-3 w-full py-3 rounded-lg border border-neutral-800/50 bg-neutral-900/50 hover:bg-neutral-800/50 hover:border-neutral-700/50 transition-all text-sm text-neutral-300">
                    <GoogleIcon /> {tr('continueWithGoogle')}</a>
                )}
              </div>
            </>
          )}
        </div>

      </div>

      </main>
    </div>
  );
}

function GithubIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.3 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 21.795 24 17.295 24 12c0-6.63-5.37-12-12-12"/>
    </svg>
  );
}

function GoogleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
    </svg>
  );
}
