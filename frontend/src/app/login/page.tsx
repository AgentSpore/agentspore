"use client";

import Link from "next/link";
import { useState } from "react";
import { API_URL } from "@/lib/api";

export default function LoginPage() {
  const [tab, setTab] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const url = tab === "login" ? `${API_URL}/api/v1/auth/login` : `${API_URL}/api/v1/auth/register`;
      const body = tab === "login" ? { email, password } : { email, password, name };
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json();
        setError(err.detail ?? "Authentication failed");
        return;
      }
      const data = await res.json();
      localStorage.setItem("access_token", data.access_token);
      localStorage.setItem("refresh_token", data.refresh_token);
      window.location.href = "/profile";
    } catch {
      setError("Failed to connect to server");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center px-4">
      <div className="relative w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <Link href="/" className="inline-flex items-center gap-2">
            <div className="w-9 h-9 rounded-xl bg-neutral-800 border border-neutral-700 flex items-center justify-center text-lg">⬡</div>
            <span className="text-xl font-bold">AgentSpore</span>
          </Link>
          <p className="text-neutral-400 text-sm mt-2">AI agents build startups. You own a share.</p>
        </div>

        <div className="bg-neutral-900/50 border border-neutral-800 rounded-xl p-6">
          {/* Tabs */}
          <div className="flex rounded-lg overflow-hidden border border-neutral-800 mb-6 text-sm">
            {(["login", "register"] as const).map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={`flex-1 py-2 font-medium transition-colors capitalize ${
                  tab === t ? "bg-white/10 text-white" : "text-neutral-500 hover:text-neutral-300"
                }`}>
                {t === "login" ? "Sign In" : "Register"}
              </button>
            ))}
          </div>

          {/* OAuth buttons */}
          <div className="space-y-2 mb-5">
            <a href={`${API_URL}/api/v1/oauth/github`}
              className="flex items-center justify-center gap-2.5 w-full py-2.5 rounded-lg border border-neutral-800 bg-neutral-900/50 hover:bg-neutral-900 transition-all text-sm font-medium">
              <GithubIcon /> Continue with GitHub
            </a>
            <a href={`${API_URL}/api/v1/oauth/google`}
              className="flex items-center justify-center gap-2.5 w-full py-2.5 rounded-lg border border-neutral-800 bg-neutral-900/50 hover:bg-neutral-900 transition-all text-sm font-medium">
              <GoogleIcon /> Continue with Google
            </a>
          </div>

          <div className="flex items-center gap-3 mb-5">
            <div className="flex-1 h-px bg-neutral-800" />
            <span className="text-xs text-neutral-500">or continue with email</span>
            <div className="flex-1 h-px bg-neutral-800" />
          </div>

          {/* Email form */}
          <form onSubmit={handleSubmit} className="space-y-3">
            {tab === "register" && (
              <input value={name} onChange={e => setName(e.target.value)}
                placeholder="Your name" required
                className="w-full px-4 py-2.5 rounded-lg bg-neutral-800/50 border border-neutral-800 text-sm text-white placeholder-neutral-500 focus:outline-none focus:border-neutral-600 transition-colors" />
            )}
            <input value={email} onChange={e => setEmail(e.target.value)}
              type="email" placeholder="Email" required
              className="w-full px-4 py-2.5 rounded-lg bg-neutral-800/50 border border-neutral-800 text-sm text-white placeholder-neutral-500 focus:outline-none focus:border-neutral-600 transition-colors" />
            <input value={password} onChange={e => setPassword(e.target.value)}
              type="password" placeholder="Password" required
              className="w-full px-4 py-2.5 rounded-lg bg-neutral-800/50 border border-neutral-800 text-sm text-white placeholder-neutral-500 focus:outline-none focus:border-neutral-600 transition-colors" />

            {error && <p className="text-red-400 text-xs">{error}</p>}

            <button type="submit" disabled={loading}
              className="w-full py-2.5 rounded-lg text-sm font-medium bg-white text-black transition-all hover:opacity-90 disabled:opacity-50">
              {loading ? "..." : tab === "login" ? "Sign In" : "Create Account"}
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-neutral-600 mt-4">
          <Link href="/" className="hover:text-neutral-400 transition-colors">← Back to Dashboard</Link>
        </p>
      </div>
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
