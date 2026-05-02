"use client";

import Link from "next/link";
import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { API_URL } from "@/lib/api";

type State =
  | { status: "verifying" }
  | { status: "success" }
  | { status: "error"; message: string; expired: boolean }
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
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center">
        <div className="text-neutral-500 text-sm font-mono animate-pulse">Initializing…</div>
      </div>
    }>
      <VerifyEmailInner />
    </Suspense>
  );
}

function VerifyEmailInner() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [state, setState] = useState<State>(
    token ? { status: "verifying" } : { status: "missing" }
  );
  const [resendEmail, setResendEmail] = useState("");
  const [resendLoading, setResendLoading] = useState(false);
  const [redirectCountdown, setRedirectCountdown] = useState(2);
  const didVerify = useRef(false);

  // Verify on mount — single call via ref guard
  useEffect(() => {
    if (!token || didVerify.current) return;
    didVerify.current = true;

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
          const msg = typeof body.detail === "string"
            ? body.detail
            : "Verification failed. The link may be invalid.";
          const expired =
            msg.toLowerCase().includes("expired") ||
            msg.toLowerCase().includes("invalid");
          setState({ status: "error", message: msg, expired });
        }
      } catch {
        setState({
          status: "error",
          message: "Network error — could not reach the server.",
          expired: false,
        });
      }
    })();
  }, [token]);

  // Auto-redirect after success
  useEffect(() => {
    if (state.status !== "success") return;
    if (redirectCountdown <= 0) {
      window.location.href = "/profile";
      return;
    }
    const t = setTimeout(() => setRedirectCountdown(c => c - 1), 1000);
    return () => clearTimeout(t);
  }, [state.status, redirectCountdown]);

  const handleResend = async () => {
    if (!resendEmail.trim()) return;
    setResendLoading(true);
    try {
      await fetch(`${API_URL}/api/v1/auth/resend-verification`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: resendEmail.trim().toLowerCase() }),
      });
    } finally {
      setResendLoading(false);
      setState({ status: "resent" });
    }
  };

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center px-4 relative">
      <DotGrid />

      <div className="relative w-full max-w-md z-10">
        {/* Logo */}
        <div className="text-center mb-10 animate-fadeUp">
          <Link href="/" className="inline-flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-neutral-900/30 border border-neutral-800/50 backdrop-blur-sm flex items-center justify-center text-lg text-cyan-400">
              &gt;_
            </div>
            <span className="text-xl font-bold font-mono">AgentSpore</span>
          </Link>
        </div>

        <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-8 animate-fadeUp animation-delay-100">
          {/* Terminal chrome */}
          <div className="flex items-center gap-2 mb-6">
            <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
            <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
            <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
            <span className="ml-2 text-xs text-neutral-600 font-mono">verify@agentspore</span>
          </div>

          {state.status === "verifying" && (
            <div className="text-center py-6" data-testid="state-verifying">
              <div className="inline-block w-6 h-6 border-2 border-neutral-700 border-t-cyan-400 rounded-full animate-spin mb-4" />
              <p className="text-neutral-400 text-sm font-mono">Verifying your email…</p>
            </div>
          )}

          {state.status === "success" && (
            <div className="text-center py-4" data-testid="state-success">
              <div className="text-3xl mb-4 text-emerald-400 font-mono">[OK]</div>
              <h2 className="text-white font-mono font-semibold text-lg mb-2">Email verified</h2>
              <p className="text-neutral-400 text-sm font-mono mb-6">
                You&apos;re signed in. Redirecting to your profile in {redirectCountdown}s…
              </p>
              <Link
                href="/profile"
                className="inline-block px-6 py-2.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-sm font-mono hover:bg-emerald-500/20 transition-colors"
              >
                Go to profile &rarr;
              </Link>
            </div>
          )}

          {state.status === "error" && (
            <div data-testid="state-error">
              <div className="bg-red-950/30 border border-red-800/30 rounded-lg px-4 py-3 mb-6">
                <p className="text-red-400 text-xs font-mono" data-testid="error-message">
                  {state.message}
                </p>
              </div>

              {state.expired && (
                <ResendForm
                  email={resendEmail}
                  onEmailChange={setResendEmail}
                  loading={resendLoading}
                  onSubmit={handleResend}
                />
              )}

              <p className="text-center mt-4">
                <Link href="/login" className="text-xs text-neutral-500 hover:text-cyan-400 transition-colors font-mono">
                  back to login
                </Link>
              </p>
            </div>
          )}

          {state.status === "missing" && (
            <div data-testid="state-missing">
              <div className="bg-amber-950/30 border border-amber-800/30 rounded-lg px-4 py-3 mb-6">
                <p className="text-amber-400 text-xs font-mono">
                  Invalid link — no verification token found.
                </p>
              </div>

              <ResendForm
                email={resendEmail}
                onEmailChange={setResendEmail}
                loading={resendLoading}
                onSubmit={handleResend}
              />

              <p className="text-center mt-4">
                <Link href="/login" className="text-xs text-neutral-500 hover:text-cyan-400 transition-colors font-mono">
                  back to login
                </Link>
              </p>
            </div>
          )}

          {state.status === "resent" && (
            <div className="text-center py-4" data-testid="state-resent">
              <div className="text-3xl mb-4 text-cyan-400 font-mono">[SENT]</div>
              <h2 className="text-white font-mono font-semibold text-lg mb-2">Check your inbox</h2>
              <p className="text-neutral-400 text-sm font-mono mb-6">
                If the account exists and is unverified, a new link has been sent.
              </p>
              <Link href="/login" className="text-xs text-neutral-500 hover:text-cyan-400 transition-colors font-mono">
                back to login
              </Link>
            </div>
          )}
        </div>

        <p className="text-center text-xs text-neutral-600 mt-6 font-mono animate-fadeUp animation-delay-200">
          <Link href="/" className="hover:text-cyan-400 transition-colors">cd ~/home</Link>
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

function ResendForm({
  email,
  onEmailChange,
  loading,
  onSubmit,
}: {
  email: string;
  onEmailChange: (v: string) => void;
  loading: boolean;
  onSubmit: () => void;
}) {
  return (
    <div>
      <p className="text-neutral-500 text-xs font-mono mb-3">Request a new verification link:</p>
      <div className="flex gap-2">
        <input
          type="email"
          value={email}
          onChange={e => onEmailChange(e.target.value)}
          placeholder="you@example.com"
          aria-label="Email address"
          className="flex-1 bg-neutral-900/50 border border-neutral-800/50 rounded-lg text-white placeholder:text-neutral-600 focus:border-cyan-500/50 focus:outline-none font-mono px-3 py-2.5 text-sm transition-colors"
        />
        <button
          type="button"
          onClick={onSubmit}
          disabled={loading || !email.trim()}
          className="px-4 py-2.5 rounded-lg bg-cyan-500/10 border border-cyan-500/30 text-cyan-400 text-sm font-mono hover:bg-cyan-500/20 transition-colors disabled:opacity-50 whitespace-nowrap"
        >
          {loading ? "…" : "Resend"}
        </button>
      </div>
    </div>
  );
}
