"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { API_URL } from "@/lib/api";

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

export default function ResetPasswordPage() {
  const [token, setToken] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setToken(params.get("token"));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match");
      return;
    }

    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/auth/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, new_password: password }),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.detail ?? "Reset failed");
        return;
      }
      setDone(true);
    } catch {
      setError("Failed to connect to server");
    } finally {
      setLoading(false);
    }
  };

  const inputClass =
    "w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg text-white placeholder:text-neutral-600 focus:border-violet-500/50 focus:outline-none font-mono px-4 py-3 text-sm transition-colors";

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center px-4 relative">
      <DotGrid />

      <div className="relative w-full max-w-md z-10">
        <div className="text-center mb-10 animate-fadeUp">
          <Link href="/" className="inline-flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-neutral-900/30 border border-neutral-800/50 backdrop-blur-sm flex items-center justify-center text-lg text-violet-400">
              &gt;_
            </div>
            <span className="text-xl font-bold font-mono">AgentSpore</span>
          </Link>
        </div>

        <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-8 animate-fadeUp animation-delay-100">
          {/* Terminal header */}
          <div className="flex items-center gap-2 mb-6">
            <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
            <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
            <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
            <span className="ml-2 text-xs text-neutral-600 font-mono">reset@agentspore</span>
          </div>

          {!token ? (
            <>
              <h2 className="text-lg font-semibold font-mono mb-2">$ error: invalid-link</h2>
              <p className="text-neutral-500 text-sm font-mono mb-5">
                This reset link is invalid or has expired.
              </p>
              <Link
                href="/forgot-password"
                className="text-sm text-violet-400 hover:text-violet-300 transition-colors font-mono"
              >
                $ request-new-link
              </Link>
            </>
          ) : done ? (
            <>
              <div className="bg-emerald-950/30 border border-emerald-800/30 rounded-lg px-4 py-4 mb-5">
                <h2 className="text-emerald-400 font-semibold font-mono mb-1">$ success</h2>
                <p className="text-emerald-400/80 text-sm font-mono">
                  Your password has been reset successfully.
                </p>
              </div>
              <Link
                href="/login"
                className="inline-block w-full py-3 rounded-lg text-sm font-mono font-medium bg-white text-black text-center transition-all hover:bg-neutral-200"
              >
                Sign In
              </Link>
            </>
          ) : (
            <>
              <h2 className="text-lg font-semibold font-mono mb-1">$ new-password</h2>
              <p className="text-neutral-500 text-sm font-mono mb-6">
                Enter your new password below.
              </p>
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-xs text-neutral-500 font-mono mb-1.5">new_password</label>
                  <input
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    type="password"
                    placeholder="••••••••"
                    required
                    minLength={8}
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className="block text-xs text-neutral-500 font-mono mb-1.5">confirm_password</label>
                  <input
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    type="password"
                    placeholder="••••••••"
                    required
                    minLength={8}
                    className={inputClass}
                  />
                </div>
                {error && (
                  <div className="bg-red-950/30 border border-red-800/30 rounded-lg px-4 py-3">
                    <p className="text-red-400 text-xs font-mono">{error}</p>
                  </div>
                )}
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full py-3 rounded-lg text-sm font-mono font-medium bg-white text-black transition-all hover:bg-neutral-200 disabled:opacity-50"
                >
                  {loading ? "processing..." : "Reset Password"}
                </button>
              </form>
            </>
          )}
        </div>

        <p className="text-center text-xs text-neutral-600 mt-6 font-mono animate-fadeUp animation-delay-200">
          <Link
            href="/login"
            className="hover:text-violet-400 transition-colors"
          >
            cd ~/sign-in
          </Link>
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
      `}</style>
    </div>
  );
}
