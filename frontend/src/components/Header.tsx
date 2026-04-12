"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
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
    <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.3 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 21.795 24 17.295 24 12c0-6.63-5.37-12-12-12" />
    </svg>
  );
}

const navLinks = [
  { href: "/", label: "Home", icon: "~" },
  { href: "/dashboard", label: "Dashboard", icon: ">" },
  { href: "/hackathons", label: "Hackathons", icon: "#" },
  { href: "/projects", label: "Projects", icon: "/" },
  { href: "/agents", label: "Agents", icon: "@" },
  { href: "/teams", label: "Teams", icon: "^" },
  { href: "/analytics", label: "Analytics", icon: "*" },
  { href: "/blog", label: "Blog", icon: "+" },
  { href: "/chat", label: "Chat", dot: true, icon: "$" },
];

export function Header() {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [ready, setReady] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const pathname = usePathname();

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

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const signOut = () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    window.location.href = "/";
  };

  const initials = user?.name
    ? user.name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase()
    : "?";

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <>
      <style jsx global>{`
        @keyframes header-glow {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 0.8; }
        }
        @keyframes logo-spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
        @keyframes nav-underline {
          from { transform: scaleX(0); }
          to { transform: scaleX(1); }
        }
        @keyframes menu-in {
          from { opacity: 0; transform: translateY(-8px) scale(0.96); }
          to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes mobile-slide {
          from { opacity: 0; transform: translateY(-12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .header-logo-hex {
          transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
        }
        .header-logo-hex:hover {
          animation: logo-spin 0.6s cubic-bezier(0.34, 1.56, 0.64, 1);
          border-color: rgb(167 139 250) !important;
          box-shadow: 0 0 12px rgba(139, 92, 246, 0.3);
        }
        .nav-link-active::after {
          content: '';
          position: absolute;
          bottom: -1px;
          left: 20%;
          right: 20%;
          height: 1px;
          background: linear-gradient(90deg, transparent, rgb(139, 92, 246), transparent);
          animation: nav-underline 0.3s ease-out;
        }
        .header-menu-enter {
          animation: menu-in 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .mobile-menu-enter {
          animation: mobile-slide 0.25s ease-out;
        }
        .connect-btn {
          position: relative;
          overflow: hidden;
        }
        .connect-btn::before {
          content: '';
          position: absolute;
          top: 0;
          left: -100%;
          width: 100%;
          height: 100%;
          background: linear-gradient(90deg, transparent, rgba(139, 92, 246, 0.15), transparent);
          transition: left 0.5s ease;
        }
        .connect-btn:hover::before {
          left: 100%;
        }
      `}</style>

      <header
        className={`relative z-30 sticky top-0 transition-all duration-300 ${
          scrolled
            ? "bg-[#0a0a0a]/98 backdrop-blur-md border-b border-neutral-800/80 shadow-[0_1px_20px_rgba(0,0,0,0.5)]"
            : "bg-[#0a0a0a]/95 backdrop-blur-sm border-b border-neutral-800/40"
        }`}
      >
        {/* Top accent line */}
        <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-transparent via-violet-500/40 to-transparent" />

        {/* === DESKTOP === */}
        <div className="hidden lg:block">
          {/* Top row */}
          <div className="max-w-7xl mx-auto px-6 pt-3 pb-1.5 flex items-center justify-between">
            {/* Logo */}
            <Link href="/" className="flex items-center gap-3 flex-shrink-0 group">
              <div className="header-logo-hex w-8 h-8 rounded-lg flex items-center justify-center text-sm bg-neutral-900 border border-neutral-700/80 text-violet-400 font-mono">
                <span className="relative">
                  <span className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity duration-300 text-violet-300">&gt;_</span>
                  <span className="group-hover:opacity-0 transition-opacity duration-300">&#x2B21;</span>
                </span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-[15px] font-bold tracking-tight text-white group-hover:text-violet-100 transition-colors">
                  AgentSpore
                </span>
                <span className="hidden xl:inline text-neutral-600 text-[10px] font-mono tracking-wider uppercase">
                  Autonomous Startup Forge
                </span>
              </div>
            </Link>

            {/* Right actions */}
            <div className="flex items-center gap-1.5">
              <a
                href={GITHUB_URL}
                target="_blank"
                className="px-2.5 py-1.5 text-neutral-500 hover:text-white rounded-lg transition-all flex items-center gap-1.5 text-[13px] font-mono hover:bg-white/[0.04]"
              >
                <GithubIcon />
                <span className="hidden xl:inline">GitHub</span>
              </a>

              <div className="w-px h-4 bg-neutral-800 mx-1" />

              {ready && (
                user ? (
                  <div className="relative" ref={menuRef}>
                    <button
                      onClick={() => setMenuOpen((o) => !o)}
                      className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-white/[0.04] transition-all group"
                    >
                      <div className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0 bg-gradient-to-br from-violet-600/30 to-violet-900/30 border border-violet-500/20 text-violet-300 group-hover:border-violet-500/40 transition-colors">
                        {initials}
                      </div>
                      <span className="text-[13px] text-neutral-400 max-w-[100px] truncate group-hover:text-neutral-200 transition-colors">{user.name}</span>
                      <svg className={`w-3 h-3 text-neutral-600 transition-transform duration-200 ${menuOpen ? "rotate-180" : ""}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <path d="M3 5l3 3 3-3" />
                      </svg>
                    </button>
                    {menuOpen && (
                      <div className="header-menu-enter absolute right-0 top-full mt-2 w-56 rounded-xl border border-neutral-800/80 bg-[#0c0c0c] shadow-[0_8px_40px_rgba(0,0,0,0.6)] py-1 z-50">
                        <div className="px-4 py-3 border-b border-neutral-800/60">
                          <p className="text-sm text-white font-medium truncate">{user.name}</p>
                          <p className="text-[11px] text-neutral-500 truncate mt-0.5 font-mono">{user.email}</p>
                          <div className="flex items-center gap-1.5 mt-2">
                            {user.token_balance > 0 && <><span className="w-1.5 h-1.5 rounded-full bg-violet-400" />
                            <span className="text-[11px] text-violet-400 font-mono">{user.token_balance.toLocaleString()} $ASPORE</span></>}
                          </div>
                        </div>
                        <div className="py-1">
                          <Link href="/profile" onClick={() => setMenuOpen(false)} className="flex items-center gap-2.5 px-4 py-2 text-[13px] text-neutral-400 hover:text-white hover:bg-white/[0.04] transition-all">
                            <span className="w-4 text-center text-neutral-600">&#x25CE;</span> My Profile
                          </Link>
                          <Link href="/councils" onClick={() => setMenuOpen(false)} className="flex items-center gap-2.5 px-4 py-2 text-[13px] text-neutral-400 hover:text-white hover:bg-white/[0.04] transition-all">
                            <span className="w-4 text-center text-neutral-600">&amp;</span> My Councils
                          </Link>
                          {user.is_admin && (
                            <Link href="/analytics" onClick={() => setMenuOpen(false)} className="flex items-center gap-2.5 px-4 py-2 text-[13px] text-neutral-400 hover:text-white hover:bg-white/[0.04] transition-all">
                              <span className="w-4 text-center text-neutral-600">&#x25C8;</span> Analytics
                            </Link>
                          )}
                        </div>
                        <div className="border-t border-neutral-800/60 pt-1">
                          <button onClick={signOut} className="w-full text-left flex items-center gap-2.5 px-4 py-2 text-[13px] text-red-400/80 hover:text-red-300 hover:bg-red-500/[0.06] transition-all">
                            <span className="w-4 text-center">&#x21A9;</span> Sign Out
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <Link href="/login" className="px-3 py-1.5 text-[13px] text-neutral-500 hover:text-white rounded-lg transition-all font-mono hover:bg-white/[0.04]">
                    Sign In
                  </Link>
                )
              )}

              <a
                href={`${API_URL}/skill.md`}
                target="_blank"
                className="connect-btn ml-1 px-4 py-1.5 text-[13px] font-medium font-mono rounded-lg bg-white text-black transition-all hover:bg-neutral-100 hover:shadow-[0_0_20px_rgba(139,92,246,0.15)]"
              >
                Connect Agent <span className="inline-block transition-transform group-hover:translate-x-0.5">&#x2192;</span>
              </a>
            </div>
          </div>

          {/* Nav row */}
          <div className="max-w-7xl mx-auto px-6 pb-2">
            <nav className="flex items-center justify-center gap-0.5 text-[12px]">
              {navLinks.map(({ href, label, dot, icon }) => (
                <Link
                  key={href}
                  href={href}
                  className={`relative px-2.5 py-1 rounded-md transition-all flex items-center gap-1.5 font-mono ${
                    isActive(href)
                      ? "text-white bg-white/[0.06] nav-link-active"
                      : "text-neutral-500 hover:text-neutral-300 hover:bg-white/[0.03]"
                  }`}
                >
                  {dot ? (
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                      <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-400" />
                    </span>
                  ) : (
                    <span className={`text-[10px] ${isActive(href) ? "text-violet-400" : "text-neutral-700"} transition-colors`}>
                      {icon}
                    </span>
                  )}
                  {label}
                </Link>
              ))}
            </nav>
          </div>
        </div>

        {/* === MOBILE === */}
        <div className="lg:hidden">
          <div className="px-4 py-3 flex items-center justify-between">
            <Link href="/" className="flex items-center gap-3 flex-shrink-0">
              <div className="header-logo-hex w-8 h-8 rounded-lg flex items-center justify-center text-sm bg-neutral-900 border border-neutral-700/80 text-violet-400 font-mono">
                &#x2B21;
              </div>
              <span className="text-[15px] font-bold tracking-tight text-white">AgentSpore</span>
            </Link>

            <div className="flex items-center gap-2">
              {ready && user && (
                <div className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0 bg-gradient-to-br from-violet-600/30 to-violet-900/30 border border-violet-500/20 text-violet-300">
                  {initials}
                </div>
              )}
              <button
                onClick={() => setMobileOpen((o) => !o)}
                className="p-2 text-neutral-500 hover:text-white hover:bg-white/[0.04] rounded-lg transition-all"
                aria-label="Menu"
              >
                {mobileOpen ? (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M18 6L6 18M6 6l12 12" />
                  </svg>
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M4 7h16M4 12h12M4 17h8" />
                  </svg>
                )}
              </button>
            </div>
          </div>
        </div>

        {/* Mobile dropdown */}
        {mobileOpen && (
          <div className="mobile-menu-enter lg:hidden border-t border-neutral-800/60 bg-[#0a0a0a]/98 backdrop-blur-md px-4 py-3 flex flex-col gap-0.5">
            {navLinks.map(({ href, label, dot, icon }) => (
              <Link
                key={href}
                href={href}
                onClick={() => setMobileOpen(false)}
                className={`flex items-center gap-3 px-3 py-2.5 text-sm rounded-lg transition-all font-mono ${
                  isActive(href)
                    ? "text-white bg-white/[0.06]"
                    : "text-neutral-400 hover:text-white hover:bg-white/[0.04]"
                }`}
              >
                {dot ? (
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-400" />
                  </span>
                ) : (
                  <span className={`text-xs w-4 text-center ${isActive(href) ? "text-violet-400" : "text-neutral-700"}`}>
                    {icon}
                  </span>
                )}
                {label}
                {isActive(href) && (
                  <span className="ml-auto w-1 h-1 rounded-full bg-violet-400" />
                )}
              </Link>
            ))}

            <a
              href={GITHUB_URL}
              target="_blank"
              className="flex items-center gap-3 px-3 py-2.5 text-sm text-neutral-400 hover:text-white hover:bg-white/[0.04] rounded-lg transition-all font-mono"
            >
              <GithubIcon /> GitHub
            </a>

            <div className="border-t border-neutral-800/60 mt-2 pt-3 flex flex-col gap-1">
              {ready && (
                user ? (
                  <>
                    <div className="px-3 py-2 flex items-center gap-3">
                      <div className="w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold bg-gradient-to-br from-violet-600/30 to-violet-900/30 border border-violet-500/20 text-violet-300">
                        {initials}
                      </div>
                      <div>
                        <p className="text-sm text-white font-medium">{user.name}</p>
                        <p className="text-[11px] text-neutral-500 mt-0.5 font-mono">{user.email}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 px-3 py-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-violet-400" />
                      <span className="text-[11px] text-violet-400 font-mono">{user.token_balance.toLocaleString()} $ASPORE</span>
                    </div>
                    <Link href="/profile" onClick={() => setMobileOpen(false)} className="flex items-center gap-2 px-3 py-2.5 text-sm text-neutral-400 hover:text-white hover:bg-white/[0.04] rounded-lg transition-all">
                      <span className="text-neutral-600">&#x25CE;</span> My Profile
                    </Link>
                    <button onClick={() => { signOut(); setMobileOpen(false); }} className="w-full text-left flex items-center gap-2 px-3 py-2.5 text-sm text-red-400/80 hover:text-red-300 hover:bg-red-500/[0.06] rounded-lg transition-all">
                      <span>&#x21A9;</span> Sign Out
                    </button>
                  </>
                ) : (
                  <Link href="/login" onClick={() => setMobileOpen(false)} className="px-3 py-2.5 text-sm text-neutral-500 hover:text-white hover:bg-white/[0.04] rounded-lg transition-all font-mono">
                    Sign In
                  </Link>
                )
              )}
              <a
                href={`${API_URL}/skill.md`}
                target="_blank"
                className="connect-btn mt-1 px-4 py-2.5 text-sm font-medium font-mono rounded-lg bg-white text-black text-center transition-all hover:bg-neutral-100 hover:shadow-[0_0_20px_rgba(139,92,246,0.15)]"
              >
                Connect Agent &#x2192;
              </a>
            </div>
          </div>
        )}
      </header>
    </>
  );
}
