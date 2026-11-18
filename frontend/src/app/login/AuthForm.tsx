"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { accountMessages, displayAccountLabel } from '@/lib/i18n/account';
import { AUTH_FLOW_MESSAGES } from '@/lib/i18n/authFlow';
import { API_URL } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { OAuthButtons } from "./OAuthButtons";
import { ResendVerification } from "./VerificationNotice";

const AUTH_TIMEOUT_MS = 15000;

type AuthFormProps = {
  tab: "login" | "register";
  onTabChange: (tab: "login" | "register") => void;
  nextUrl: string;
  defaultEmail: string;
  focusEmail: boolean;
  onRegistered: (email: string) => void;
};

/** Tabs, credentials form and third-party sign-in; reports a pending-confirmation registration to its parent. */
export function AuthForm({ tab, onTabChange, nextUrl, defaultEmail, focusEmail, onRegistered }: AuthFormProps) {
  const { locale } = useLocale();
  const tr = useTranslations(accountMessages);
  const flowTr = useTranslations(AUTH_FLOW_MESSAGES);
  const { info: toastInfo } = useToast();
  const [email, setEmail] = useState(defaultEmail);
  const [password, setPassword] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [slowHint, setSlowHint] = useState(false);
  const [unverified, setUnverified] = useState<string | null>(null);
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (focusEmail) emailRef.current?.focus(); }, [focusEmail]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setUnverified(null);
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
      if (action === "login" && res.status === 403) {
        setUnverified(email);
        return;
      }
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
      onRegistered(email.trim());
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
    <>
          <div className="flex rounded-lg overflow-hidden border border-neutral-800/50 mb-6 text-sm">
            {(["login", "register"] as const).map(t => (
              <button key={t} type="button" aria-pressed={tab === t} onClick={() => { onTabChange(t); setPasswordVisible(false); setUnverified(null); }}
                className={`min-h-11 flex-1 py-2.5 font-medium transition-colors ${
                  tab === t
                    ? "bg-violet-500/10 text-violet-400 border-b-2 border-violet-400"
                    : "text-neutral-400 hover:text-neutral-300"
                }`}>
                {t === 'login' ? flowTr('tabSignIn') : flowTr('tabRegister')}
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
              <input id="auth-email" ref={emailRef} value={email} onChange={e => setEmail(e.target.value)}
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

            {unverified !== null && (
              <div className="bg-amber-950/20 border border-amber-800/30 rounded-lg px-4 py-3">
                <p role="alert" className="text-amber-300 text-xs leading-5">{flowTr('unverifiedMessage')}</p>
                <ResendVerification email={unverified} />
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
      <OAuthButtons nextUrl={nextUrl} />
    </>
  );
}
