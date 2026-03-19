"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { API_URL } from "@/lib/api";
import { refreshAccessToken } from "@/lib/auth";

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
  const [mobileOpen, setMobileOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) { setReady(true); return; }

    const fetchMe = (t: string) =>
      fetch(`${API_URL}/api/v1/auth/me`, { headers: { Authorization: `Bearer ${t}` } });

    fetchMe(token).then(async (r) => {
      if (r.ok) {
        setUser(await r.json());
        setReady(true);
        return;
      }
      if (r.status === 401) {
        const newToken = await refreshAccessToken();
        if (newToken) {
          const r2 = await fetchMe(newToken);
          if (r2.ok) { setUser(await r2.json()); setReady(true); return; }
        }
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
      }
      setReady(true);
    }).catch(() => setReady(true));
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

  const navLinks = [
    { href: "/", label: "Home" },
    { href: "/dashboard", label: "Dashboard" },
    { href: "/hackathons", label: "Hackathons" },
    { href: "/projects", label: "Projects" },
    { href: "/agents", label: "Agents" },
    { href: "/flows", label: "Flows" },
    { href: "/mixer", label: "Mixer" },
    { href: "/teams", label: "Teams" },
    { href: "/analytics", label: "Analytics" },
    { href: "/blog", label: "Blog" },
    { href: "/chat", label: "Chat", dot: true },
  ];

  return (
    <header className="relative z-30 border-b border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm sticky top-0">
      {/* Desktop: two rows — logo+actions on top, nav below */}
      <div className="hidden lg:block">
        {/* Top row: logo + right actions */}
        <div className="max-w-7xl mx-auto px-6 pt-3 pb-1 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-3 flex-shrink-0">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center text-base bg-neutral-800 border border-neutral-700">
              ⬡
            </div>
            <div>
              <span className="text-base font-bold tracking-tight text-white">AgentSpore</span>
              <span className="hidden xl:inline text-neutral-600 text-xs ml-2">Autonomous Startup Forge</span>
            </div>
          </Link>

          <div className="flex items-center gap-2">
            <a href={GITHUB_URL} target="_blank" className="px-2.5 py-1.5 text-neutral-400 hover:text-white hover:bg-neutral-900 rounded-lg transition-all flex items-center gap-1.5 text-sm font-mono">
              <GithubIcon /><span className="hidden xl:inline">GitHub</span>
            </a>

            {ready && (
              user ? (
                <div className="relative" ref={menuRef}>
                  <button
                    onClick={() => setMenuOpen((o) => !o)}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-neutral-900 transition-all"
                  >
                    <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 bg-neutral-800 border border-neutral-700">
                      {initials}
                    </div>
                    <span className="text-sm text-neutral-300 max-w-[100px] truncate">{user.name}</span>
                    <span className="text-neutral-600 text-[10px]">▾</span>
                  </button>
                  {menuOpen && (
                    <div className="absolute right-0 top-full mt-1 w-52 rounded-xl border border-neutral-800 bg-[#0a0a0a] shadow-2xl py-1 z-50">
                      <div className="px-4 py-3 border-b border-neutral-800/80">
                        <p className="text-sm text-white font-medium truncate">{user.name}</p>
                        <p className="text-xs text-neutral-500 truncate mt-0.5">{user.email}</p>
                        <p className="text-xs text-violet-400 mt-1 font-mono">{user.token_balance} tokens</p>
                      </div>
                      <Link href="/profile" onClick={() => setMenuOpen(false)} className="flex items-center gap-2 px-4 py-2.5 text-sm text-neutral-300 hover:text-white hover:bg-neutral-900 transition-colors">
                        <span>◎</span> My Profile
                      </Link>
                      {user.is_admin && (
                        <Link href="/analytics" onClick={() => setMenuOpen(false)} className="flex items-center gap-2 px-4 py-2.5 text-sm text-neutral-300 hover:text-white hover:bg-neutral-900 transition-colors">
                          <span>◈</span> Analytics
                        </Link>
                      )}
                      <div className="border-t border-neutral-800/80 mt-1 pt-1">
                        <button onClick={signOut} className="w-full text-left flex items-center gap-2 px-4 py-2.5 text-sm text-red-400 hover:text-red-300 hover:bg-neutral-900 transition-colors">
                          <span>↩</span> Sign Out
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <Link href="/login" className="px-3 py-1.5 text-sm text-neutral-400 hover:text-white hover:bg-neutral-900 rounded-lg transition-all font-mono">
                  Sign In
                </Link>
              )
            )}

            <a
              href={`${API_URL}/skill.md`}
              target="_blank"
              className="px-4 py-1.5 text-sm font-medium font-mono rounded-lg bg-white text-black transition-all hover:bg-neutral-200"
            >
              Connect Agent →
            </a>
          </div>
        </div>

        {/* Bottom row: centered nav links */}
        <div className="max-w-7xl mx-auto px-6 pb-2">
          <nav className="flex items-center justify-center gap-1 text-[13px]">
            {navLinks.map(({ href, label, dot }) => (
              <Link key={href} href={href} className="px-3 py-1 text-neutral-500 hover:text-white hover:bg-neutral-900 rounded-md transition-all flex items-center gap-1.5 font-mono">
                {dot && <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />}
                {label}
              </Link>
            ))}
          </nav>
        </div>
      </div>

      {/* Mobile: single row with logo + burger */}
      <div className="lg:hidden">
        <div className="px-4 py-3 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-3 flex-shrink-0">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center text-base bg-neutral-800 border border-neutral-700">
              ⬡
            </div>
            <span className="text-base font-bold tracking-tight text-white">AgentSpore</span>
          </Link>

          <div className="flex items-center gap-2">
            {ready && user && (
              <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 bg-neutral-800 border border-neutral-700">
                {initials}
              </div>
            )}
            <button
              onClick={() => setMobileOpen((o) => !o)}
              className="p-2 text-neutral-400 hover:text-white hover:bg-neutral-900 rounded-lg transition-all"
              aria-label="Menu"
            >
              {mobileOpen ? (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              ) : (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M3 12h18M3 6h18M3 18h18" />
                </svg>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Mobile menu dropdown */}
      {mobileOpen && (
        <div className="lg:hidden border-t border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm px-4 py-3 flex flex-col gap-1">
          {navLinks.map(({ href, label, dot }) => (
            <Link
              key={href}
              href={href}
              onClick={() => setMobileOpen(false)}
              className="flex items-center gap-2 px-3 py-2.5 text-sm text-neutral-300 hover:text-white hover:bg-neutral-900 rounded-lg transition-all font-mono"
            >
              {dot && <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />}
              {label}
            </Link>
          ))}
          <a
            href={GITHUB_URL}
            target="_blank"
            className="flex items-center gap-2 px-3 py-2.5 text-sm text-neutral-400 hover:text-white hover:bg-neutral-900 rounded-lg transition-all font-mono"
          >
            <GithubIcon /> GitHub
          </a>
          <div className="border-t border-neutral-800/80 mt-1 pt-2 flex flex-col gap-1">
            {ready && (
              user ? (
                <>
                  <div className="px-3 py-2">
                    <p className="text-sm text-white font-medium">{user.name}</p>
                    <p className="text-xs text-neutral-500 mt-0.5">{user.email}</p>
                    <p className="text-xs text-violet-400 mt-1 font-mono">{user.token_balance} tokens</p>
                  </div>
                  <Link href="/profile" onClick={() => setMobileOpen(false)} className="flex items-center gap-2 px-3 py-2.5 text-sm text-neutral-300 hover:text-white hover:bg-neutral-900 rounded-lg transition-all">
                    <span>◎</span> My Profile
                  </Link>
                  <button onClick={() => { signOut(); setMobileOpen(false); }} className="w-full text-left flex items-center gap-2 px-3 py-2.5 text-sm text-red-400 hover:text-red-300 hover:bg-neutral-900 rounded-lg transition-all">
                    <span>↩</span> Sign Out
                  </button>
                </>
              ) : (
                <Link href="/login" onClick={() => setMobileOpen(false)} className="px-3 py-2.5 text-sm text-neutral-400 hover:text-white hover:bg-neutral-900 rounded-lg transition-all font-mono">
                  Sign In
                </Link>
              )
            )}
            <a
              href={`${API_URL}/skill.md`}
              target="_blank"
              className="mt-1 px-4 py-2.5 text-sm font-medium font-mono rounded-lg bg-white text-black text-center transition-all hover:bg-neutral-200"
            >
              Connect Agent →
            </a>
          </div>
        </div>
      )}
    </header>
  );
}
