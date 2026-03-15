"use client";

import Link from "next/link";
import { useState } from "react";
import { API_URL } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await fetch(`${API_URL}/api/v1/auth/forgot-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
    } catch {
      // silent — always show success
    } finally {
      setLoading(false);
      setSent(true);
    }
  };

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
          <h2 className="text-lg font-semibold mb-1">Reset Password</h2>
          <p className="text-neutral-400 text-sm mb-6">
            Enter your email to receive a reset link.
          </p>

          {sent ? (
            <p className="text-neutral-400 text-sm leading-relaxed">
              If an account with that email exists, we sent a password reset
              link. Check your inbox.
            </p>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-3">
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                type="email"
                placeholder="Email"
                required
                className="w-full px-4 py-2.5 rounded-lg bg-neutral-800/50 border border-neutral-800 text-sm text-white placeholder-neutral-500 focus:outline-none focus:border-neutral-600 transition-colors"
              />
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2.5 rounded-lg text-sm font-medium bg-white text-black transition-all hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "..." : "Send Reset Link"}
              </button>
            </form>
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
