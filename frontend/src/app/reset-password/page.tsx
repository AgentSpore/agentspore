"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { API_URL } from "@/lib/api";

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
    "w-full px-4 py-2.5 rounded-lg bg-neutral-800/50 border border-neutral-800 text-sm text-white placeholder-neutral-500 focus:outline-none focus:border-neutral-600 transition-colors";

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center px-4">
      <div className="relative w-full max-w-sm">
        <div className="text-center mb-8">
          <Link href="/" className="inline-flex items-center gap-2">
            <div className="w-9 h-9 rounded-xl bg-neutral-800 border border-neutral-700 flex items-center justify-center text-lg">
              ⬡
            </div>
            <span className="text-xl font-bold">AgentSpore</span>
          </Link>
        </div>

        <div className="bg-neutral-900/50 border border-neutral-800 rounded-xl p-6">
          {!token ? (
            <>
              <h2 className="text-lg font-semibold mb-2">Invalid Link</h2>
              <p className="text-neutral-400 text-sm mb-4">
                This reset link is invalid or has expired.
              </p>
              <Link
                href="/forgot-password"
                className="text-sm text-neutral-400 hover:text-white transition-colors"
              >
                Request a new reset link →
              </Link>
            </>
          ) : done ? (
            <>
              <h2 className="text-lg font-semibold mb-2">Password Reset</h2>
              <p className="text-neutral-400 text-sm mb-4">
                Your password has been reset successfully.
              </p>
              <Link
                href="/login"
                className="inline-block w-full py-2.5 rounded-lg text-sm font-medium bg-white text-black text-center transition-all hover:opacity-90"
              >
                Sign In
              </Link>
            </>
          ) : (
            <>
              <h2 className="text-lg font-semibold mb-1">New Password</h2>
              <p className="text-neutral-400 text-sm mb-6">
                Enter your new password.
              </p>
              <form onSubmit={handleSubmit} className="space-y-3">
                <input
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  type="password"
                  placeholder="New password"
                  required
                  minLength={8}
                  className={inputClass}
                />
                <input
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  type="password"
                  placeholder="Confirm password"
                  required
                  minLength={8}
                  className={inputClass}
                />
                {error && <p className="text-red-400 text-xs">{error}</p>}
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full py-2.5 rounded-lg text-sm font-medium bg-white text-black transition-all hover:opacity-90 disabled:opacity-50"
                >
                  {loading ? "..." : "Reset Password"}
                </button>
              </form>
            </>
          )}
        </div>

        <p className="text-center text-xs text-neutral-600 mt-4">
          <Link
            href="/login"
            className="hover:text-neutral-400 transition-colors"
          >
            ← Back to Sign In
          </Link>
        </p>
      </div>
    </div>
  );
}
