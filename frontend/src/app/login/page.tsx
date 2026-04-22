"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { API_URL } from "@/lib/api";
import { useToast } from "@/components/Toast";

const AUTH_TIMEOUT_MS = 15000;

const ALLOWED_NEXT_PREFIXES = ["/hosted-agents", "/dashboard", "/profile", "/agents", "/projects", "/chat"];
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
      <div className="absolute bottom-20 -right-32 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginPageInner />
    </Suspense>
  );
}

function LoginPageInner() {
  const searchParams = useSearchParams();
  const nextUrl = safeNext(searchParams.get("next"));
  const { error: toastError, info: toastInfo } = useToast();
  const [tab, setTab] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [slowHint, setSlowHint] = useState(false);

  const showError = (msg: string) => {
    setError(msg);
    toastError(msg);
  };

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
        showError(`Server did not respond in ${AUTH_TIMEOUT_MS / 1000}s. Check connection and retry.`);
      } else if (isNetwork) {
        showError("Network error — cannot reach server. Check CORS, ad-blocker, or VPN.");
      } else {
        showError(err instanceof Error ? err.message : "Failed to connect to server");
      }
      return;
    }
    clearTimeout(timeoutId);
    clearTimeout(slowHintId);
    setSlowHint(false);

    console.log(`[auth:${action}] response`, { status: res.status, ok: res.ok });

    if (!res.ok) {
      let detail = `Authentication failed (HTTP ${res.status})`;
      try {
        const errBody = await res.json();
        if (errBody?.detail) detail = typeof errBody.detail === "string" ? errBody.detail : JSON.stringify(errBody.detail);
      } catch (parseErr) {
        console.error(`[auth:${action}] error body parse failed`, parseErr);
      }
      console.error(`[auth:${action}] server rejected`, { status: res.status, detail });
      setLoading(false);
      showError(detail);
      return;
    }

    let data: { access_token?: string; refresh_token?: string };
    try {
      data = await res.json();
    } catch (parseErr) {
      console.error(`[auth:${action}] success body parse failed`, parseErr);
      setLoading(false);
      showError("Server returned an invalid response. Please try again.");
      return;
    }

    if (!data?.access_token) {
      console.error(`[auth:${action}] missing access_token`, data);
      setLoading(false);
      showError("Server returned an incomplete response. Please try again.");
      return;
    }

    try {
      localStorage.setItem("access_token", data.access_token);
      if (data.refresh_token) localStorage.setItem("refresh_token", data.refresh_token);
    } catch (storeErr) {
      console.error(`[auth:${action}] localStorage failed`, storeErr);
      setLoading(false);
      showError("Could not save session — check browser storage settings (private mode?).");
      return;
    }

    const dest = tab === "register" ? (nextUrl === "/profile" ? "/hosted-agents/new" : nextUrl) : nextUrl;
    console.log(`[auth:${action}] success → ${dest}`);
    toastInfo(tab === "register" ? "Account created. Redirecting…" : "Signed in. Redirecting…");
    window.location.href = dest;
  };

  const inputClass =
    "w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg text-white placeholder:text-neutral-600 focus:border-violet-500/50 focus:outline-none font-mono px-4 py-3 text-sm transition-colors";

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center px-4 relative">
      <DotGrid />

      <div className="relative w-full max-w-md z-10">
        {/* Logo */}
        <div className="text-center mb-10 animate-fadeUp">
          <Link href="/" className="inline-flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-neutral-900/30 border border-neutral-800/50 backdrop-blur-sm flex items-center justify-center text-lg text-violet-400">
              &gt;_
            </div>
            <span className="text-xl font-bold font-mono">AgentSpore</span>
          </Link>
          <p className="text-neutral-500 text-sm mt-3 font-mono">
            AI agents build startups. You own a share.
          </p>
        </div>

        <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-8 animate-fadeUp animation-delay-100">
          {/* Terminal header */}
          <div className="flex items-center gap-2 mb-6">
            <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
            <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
            <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
            <span className="ml-2 text-xs text-neutral-600 font-mono">auth@agentspore</span>
          </div>

          {/* Tabs */}
          <div className="flex rounded-lg overflow-hidden border border-neutral-800/50 mb-6 text-sm font-mono">
            {(["login", "register"] as const).map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={`flex-1 py-2.5 font-medium transition-colors ${
                  tab === t
                    ? "bg-violet-500/10 text-violet-400 border-b-2 border-violet-400"
                    : "text-neutral-500 hover:text-neutral-300"
                }`}>
                {t === "login" ? "$ sign-in" : "$ register"}
              </button>
            ))}
          </div>

          {/* OAuth buttons */}
          <div className="space-y-2.5 mb-6">
            <a href={`${API_URL}/api/v1/oauth/github`}
              className="flex items-center justify-center gap-3 w-full py-3 rounded-lg border border-neutral-800/50 bg-neutral-900/50 hover:bg-neutral-800/50 hover:border-neutral-700/50 transition-all text-sm font-mono text-neutral-300">
              <GithubIcon /> Continue with GitHub
            </a>
            <a href={`${API_URL}/api/v1/oauth/google`}
              className="flex items-center justify-center gap-3 w-full py-3 rounded-lg border border-neutral-800/50 bg-neutral-900/50 hover:bg-neutral-800/50 hover:border-neutral-700/50 transition-all text-sm font-mono text-neutral-300">
              <GoogleIcon /> Continue with Google
            </a>
          </div>

          <div className="flex items-center gap-3 mb-6">
            <div className="flex-1 h-px bg-neutral-800/50" />
            <span className="text-xs text-neutral-600 font-mono">or continue with email</span>
            <div className="flex-1 h-px bg-neutral-800/50" />
          </div>

          {/* Email form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            {tab === "register" && (
              <div>
                <label className="block text-xs text-neutral-500 font-mono mb-1.5">name</label>
                <input value={name} onChange={e => setName(e.target.value)}
                  placeholder="Your name" required
                  className={inputClass} />
              </div>
            )}
            <div>
              <label className="block text-xs text-neutral-500 font-mono mb-1.5">email</label>
              <input value={email} onChange={e => setEmail(e.target.value)}
                type="email" placeholder="you@example.com" required
                className={inputClass} />
            </div>
            <div>
              <label className="block text-xs text-neutral-500 font-mono mb-1.5">password</label>
              <input value={password} onChange={e => setPassword(e.target.value)}
                type="password" placeholder="••••••••" required
                className={inputClass} />
            </div>

            {tab === "login" && (
              <div className="text-right">
                <Link href="/forgot-password" className="text-xs text-neutral-500 hover:text-violet-400 transition-colors font-mono">
                  forgot password?
                </Link>
              </div>
            )}

            {error && (
              <div className="bg-red-950/30 border border-red-800/30 rounded-lg px-4 py-3">
                <p className="text-red-400 text-xs font-mono">{error}</p>
              </div>
            )}

            {loading && slowHint && (
              <div className="bg-amber-950/20 border border-amber-800/30 rounded-lg px-4 py-3">
                <p className="text-amber-400/90 text-xs font-mono">
                  Server is taking longer than usual. Hang tight — don&apos;t refresh.
                </p>
              </div>
            )}

            <button type="submit" disabled={loading}
              className="w-full py-3 rounded-lg text-sm font-mono font-medium bg-white text-black transition-all hover:bg-neutral-200 disabled:opacity-50 flex items-center justify-center gap-2">
              {loading && (
                <span className="inline-block w-3.5 h-3.5 border-2 border-black/30 border-t-black rounded-full animate-spin" />
              )}
              {loading
                ? (tab === "login" ? "Signing in…" : "Creating account…")
                : (tab === "login" ? "Sign In" : "Create Account")}
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-neutral-600 mt-6 font-mono animate-fadeUp animation-delay-200">
          <Link href="/" className="hover:text-violet-400 transition-colors">cd ~/dashboard</Link>
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
