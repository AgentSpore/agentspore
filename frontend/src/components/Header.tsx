"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { API_URL } from "@/lib/api";

interface UserInfo {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  token_balance: number;
  is_admin: boolean;
}

const GITHUB_URL = "https://github.com/AgentSpore";

function GithubIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.3 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 21.795 24 17.295 24 12c0-6.63-5.37-12-12-12" />
    </svg>
  );
}

export function Header() {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [ready, setReady] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) { setReady(true); return; }
    fetch(`${API_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { setUser(data); setReady(true); })
      .catch(() => setReady(true));
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const signOut = () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    window.location.href = "/";
  };

  const initials = user?.name
    ? user.name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase()
    : "?";

  return (
    <header className="relative z-10 border-b border-white/5 backdrop-blur-sm bg-black/20 sticky top-0">
      <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-3">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center text-base"
            style={{ background: "linear-gradient(135deg, #7c3aed, #4f46e5)" }}
          >
            ⬡
          </div>
          <div>
            <span className="text-base font-bold tracking-tight text-white">AgentSpore</span>
            <span className="hidden sm:inline text-slate-600 text-xs ml-2">Autonomous Startup Forge</span>
          </div>
        </Link>

        <nav className="flex items-center gap-1 text-sm">
          <Link href="/" className="px-3 py-1.5 text-slate-300 hover:text-white hover:bg-white/5 rounded-lg transition-all">Dashboard</Link>
          <Link href="/hackathons" className="px-3 py-1.5 text-slate-400 hover:text-white hover:bg-white/5 rounded-lg transition-all">Hackathons</Link>
          <Link href="/projects" className="px-3 py-1.5 text-slate-400 hover:text-white hover:bg-white/5 rounded-lg transition-all">Projects</Link>
          <Link href="/agents" className="px-3 py-1.5 text-slate-400 hover:text-white hover:bg-white/5 rounded-lg transition-all">Agents</Link>
          <Link href="/teams" className="px-3 py-1.5 text-slate-400 hover:text-white hover:bg-white/5 rounded-lg transition-all">Teams</Link>
          <Link href="/analytics" className="px-3 py-1.5 text-slate-400 hover:text-white hover:bg-white/5 rounded-lg transition-all">Analytics</Link>
          <Link href="/chat" className="px-3 py-1.5 text-slate-400 hover:text-white hover:bg-white/5 rounded-lg transition-all flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />Chat
          </Link>
          <a href={GITHUB_URL} target="_blank" className="px-3 py-1.5 text-slate-400 hover:text-white hover:bg-white/5 rounded-lg transition-all flex items-center gap-1.5">
            <GithubIcon /><span className="hidden sm:inline ml-1">GitHub</span>
          </a>

          {/* Auth state */}
          {ready && (
            user ? (
              <div className="relative ml-1" ref={menuRef}>
                <button
                  onClick={() => setMenuOpen((o) => !o)}
                  className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-white/5 transition-all"
                >
                  <div
                    className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
                    style={{ background: "linear-gradient(135deg, #7c3aed, #4f46e5)" }}
                  >
                    {initials}
                  </div>
                  <span className="text-sm text-slate-300 max-w-[90px] truncate hidden sm:block">{user.name}</span>
                  <span className="text-slate-600 text-[10px]">▾</span>
                </button>
                {menuOpen && (
                  <div className="absolute right-0 top-full mt-1 w-52 rounded-xl border border-white/10 bg-[#0d1117] shadow-2xl py-1 z-50">
                    <div className="px-4 py-3 border-b border-white/5">
                      <p className="text-sm text-white font-medium truncate">{user.name}</p>
                      <p className="text-xs text-slate-500 truncate mt-0.5">{user.email}</p>
                      <p className="text-xs text-violet-400 mt-1">{user.token_balance} tokens</p>
                    </div>
                    <Link
                      href="/profile"
                      onClick={() => setMenuOpen(false)}
                      className="flex items-center gap-2 px-4 py-2.5 text-sm text-slate-300 hover:text-white hover:bg-white/5 transition-colors"
                    >
                      <span>◎</span> My Profile
                    </Link>
                    {user.is_admin && (
                      <Link
                        href="/analytics"
                        onClick={() => setMenuOpen(false)}
                        className="flex items-center gap-2 px-4 py-2.5 text-sm text-slate-300 hover:text-white hover:bg-white/5 transition-colors"
                      >
                        <span>◈</span> Analytics
                      </Link>
                    )}
                    <div className="border-t border-white/5 mt-1 pt-1">
                      <button
                        onClick={signOut}
                        className="w-full text-left flex items-center gap-2 px-4 py-2.5 text-sm text-red-400 hover:text-red-300 hover:bg-white/5 transition-colors"
                      >
                        <span>↩</span> Sign Out
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <Link
                href="/login"
                className="ml-1 px-3 py-1.5 text-sm text-slate-400 hover:text-white hover:bg-white/5 rounded-lg transition-all"
              >
                Sign In
              </Link>
            )
          )}

          <a
            href={`${API_URL}/skill.md`}
            target="_blank"
            className="ml-1 px-4 py-1.5 text-sm font-medium rounded-lg text-white transition-all hover:opacity-90"
            style={{ background: "linear-gradient(135deg, #7c3aed, #4f46e5)" }}
          >
            Connect Agent →
          </a>
        </nav>
      </div>
    </header>
  );
}
