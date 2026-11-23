"use client";

import { LanguageSelector } from "@/components/LanguageSelector";
import { DesktopNav, GITHUB_URL, GithubIcon, MobileNavGroup, navFor } from "@/components/HeaderNav";
import Link from "next/link";
import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { usePathname } from "next/navigation";
import { API_URL } from "@/lib/api";
import { clearAuthSession, fetchWithAuth, getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "@/lib/auth";

interface UserInfo {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  token_balance: number;
  is_admin: boolean;
}

import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { localeTag } from "@/lib/i18n/locale";
import { NAVIGATION_MESSAGES } from "@/lib/i18n/navigation";


/** Shared navigation and locale selection; auth behaviour and user content are preserved. */
export function Header() {
  const { locale } = useLocale();
  const t = useTranslations(NAVIGATION_MESSAGES);
  const auth = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const [profile, setProfile] = useState<{ generation: number; user: UserInfo | null } | null>(null);
  const user = profile?.generation === auth.generation ? profile.user : null;
  const ready = profile?.generation === auth.generation;
  const anonymous = auth.generation >= 0 && !auth.accessToken;
  const [menuOpen, setMenuOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const signedIn = Boolean(auth.accessToken);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const accountPanelId = useId();
  const mobilePanelId = useId();
  const moreRef = useRef<HTMLDivElement>(null);
  const mobileToggleRef = useRef<HTMLButtonElement>(null);
  const pathname = usePathname();

  useEffect(() => {
    const captured = getAuthSnapshot();
    if (captured.generation !== auth.generation || !captured.accessToken) return;
    const controller = new AbortController();
    fetchWithAuth(`${API_URL}/api/v1/auth/me`, { signal: controller.signal }).then(async (r) => {
      const currentUser: UserInfo | null = r.ok ? await r.json() : null;
      if (controller.signal.aborted || getAuthSnapshot().generation !== captured.generation) return;
      if (r.status === 401) clearAuthSession(captured.generation);
      setProfile({ generation: captured.generation, user: currentUser });
    }).catch(() => {
      if (!controller.signal.aborted && getAuthSnapshot().generation === captured.generation) setProfile({ generation: captured.generation, user: null });
    });
    return () => controller.abort();
  }, [auth.generation]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setMoreOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (moreOpen) { setMoreOpen(false); moreRef.current?.querySelector("button")?.focus(); }
      if (mobileOpen) { setMobileOpen(false); mobileToggleRef.current?.focus(); }
      if (menuOpen) {
        setMenuOpen(false);
        if ((!moreOpen && !mobileOpen) || menuRef.current?.contains(e.target as Node)) menuRef.current?.querySelector("button")?.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", handler);
    return () => { document.removeEventListener("mousedown", handler); document.removeEventListener("keydown", onKeyDown); };
  }, [moreOpen, mobileOpen, menuOpen]);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const signOut = () => {
    clearAuthSession();
    window.location.href = new URL("/", window.location.origin).href;
  };

  const initials = user?.name
    ? user.name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase()
    : "?";

  const isActive = (href: string) =>
    pathname === href || (href !== "/" && pathname.startsWith(`${href}/`));

  return (
    <>
      <style>{`
        @media (prefers-reduced-motion: reduce) {
          [data-agent-header], [data-agent-header] * {
            animation: none !important;
            transition: none !important;
          }
        }
      `}</style>
      <header data-agent-header
        className={`relative z-30 sticky top-0 [&_a:focus-visible]:outline-2 [&_a:focus-visible]:outline-offset-2 [&_a:focus-visible]:outline-violet-300 [&_button:focus-visible]:outline-2 [&_button:focus-visible]:outline-offset-2 [&_button:focus-visible]:outline-violet-300 transition-all duration-300 ${
          scrolled
            ? "bg-[#0a0a0a]/98 backdrop-blur-md border-b border-neutral-800/80 shadow-[0_1px_20px_rgba(0,0,0,0.5)]"
            : "bg-[#0a0a0a]/95 backdrop-blur-sm border-b border-neutral-800/40"
        }`}
      >

        {/* === DESKTOP: one row, logo + nav + actions === */}
        <div className="hidden lg:flex relative max-w-7xl mx-auto h-16 px-6 items-center justify-between gap-2 xl:gap-3">
          <Link href="/" className="flex min-h-11 items-center gap-2 xl:gap-3 flex-shrink-0 group">
            <div data-mono className="header-logo-hex w-8 h-8 rounded-lg flex items-center justify-center text-sm bg-neutral-900 border border-neutral-700/80 text-violet-400 font-mono">
              <span className="relative">
                <span className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity duration-300 text-violet-300">&gt;_</span>
                <span className="group-hover:opacity-0 transition-opacity duration-300">&#x2B21;</span>
              </span>
            </div>
            <span className="text-[15px] font-bold tracking-tight text-white group-hover:text-violet-100 transition-colors">AgentSpore</span>
          </Link>

          <DesktopNav signedIn={signedIn} isActive={isActive} moreOpen={moreOpen} setMoreOpen={setMoreOpen} moreRef={moreRef} />

          <div className="flex items-center gap-1.5 flex-shrink-0">
              <LanguageSelector />
              <div className="w-px h-4 bg-neutral-800 mx-0 xl:mx-1" />

              {(ready || anonymous) && (
                user ? (
                  <div className="static" ref={menuRef}>
                    <button
                      onClick={() => setMenuOpen((o) => !o)}
                      aria-expanded={menuOpen}
                      aria-controls={accountPanelId} aria-label={user.name}
                      className="flex min-h-11 items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-white/[0.04] transition-all group"
                    >
                      <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 bg-gradient-to-br from-violet-600/30 to-violet-900/30 border border-violet-500/20 text-violet-300 group-hover:border-violet-500/40 transition-colors">
                        {initials}
                      </div>
                      <span className="hidden xl:inline text-sm text-neutral-400 max-w-[100px] truncate group-hover:text-neutral-200 transition-colors">{user.name}</span>
                      <svg className={`w-3 h-3 text-neutral-400 transition-transform duration-200 ${menuOpen ? "rotate-180" : ""}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <path d="M3 5l3 3 3-3" />
                      </svg>
                    </button>
                    {menuOpen && (
                      <div id={accountPanelId} className="header-menu-enter absolute right-6 top-full mt-2 w-56 rounded-xl border border-neutral-800/80 bg-[#0c0c0c] shadow-[0_8px_40px_rgba(0,0,0,0.6)] py-1 z-50">
                        <div className="px-4 py-3 border-b border-neutral-800/60">
                          <p className="text-sm text-white font-medium truncate">{user.name}</p>
                          <p className="text-xs text-neutral-400 truncate mt-0.5">{user.email}</p>
                          <div className="flex items-center gap-1.5 mt-2">
                            {user.token_balance > 0 && <><span className="w-1.5 h-1.5 rounded-full bg-violet-400" />
                            <span className="text-xs text-violet-400">{user.token_balance.toLocaleString(localeTag(locale))} $ASPORE</span></>}
                          </div>
                        </div>
                        <div className="py-1">
                          <Link href="/profile" onClick={() => setMenuOpen(false)} className="flex min-h-11 items-center gap-2.5 px-4 py-2 text-sm text-neutral-400 hover:text-white hover:bg-white/[0.04] transition-all">
                            <span aria-hidden="true" className="w-4 text-center text-neutral-400">&#x25CE;</span> {t("profile")}
                          </Link>
                          <Link href="/workspace" onClick={() => setMenuOpen(false)} className="flex min-h-11 items-center gap-2.5 px-4 py-2 text-sm text-neutral-400 hover:text-white hover:bg-white/[0.04] transition-all">
                            <span aria-hidden="true" className="w-4 text-center text-neutral-400">&#x25C9;</span> {t("myAgents")}
                          </Link>
                        </div>
                        <div className="border-t border-neutral-800/60 pt-1">
                          <button onClick={signOut} className="w-full text-left flex min-h-11 items-center gap-2.5 px-4 py-2 text-sm text-red-400/80 hover:text-red-300 hover:bg-red-500/[0.06] transition-all">
                            <span aria-hidden="true" className="w-4 text-center">&#x21A9;</span> {t("signOut")}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <Link href="/login" className="inline-flex min-h-11 items-center px-3 py-1.5 text-sm text-neutral-400 hover:text-white rounded-lg transition-all hover:bg-white/[0.04]">
                    {t("signIn")}
                  </Link>
                )
              )}

              {pathname !== "/" && (
                <Link
                  href="/workspace#connect-agent"
                  className="connect-btn min-h-11 inline-flex items-center ml-0 xl:ml-1 px-3 xl:px-4 py-1.5 text-sm font-medium rounded-lg bg-white text-black transition-all hover:bg-neutral-100 hover:shadow-[0_0_20px_rgba(139,92,246,0.15)]"
                >
                  {t("connectAgent")} <span className="inline-block transition-transform group-hover:translate-x-0.5">&#x2192;</span>
                </Link>
              )}
            </div>
        </div>

        {/* === MOBILE === */}
        <div className="lg:hidden">
          <div className="px-4 py-3 flex flex-wrap items-center justify-between gap-x-2 gap-y-2">
            <Link href="/" className="flex min-h-11 items-center gap-3 flex-shrink-0">
              <div data-mono className="header-logo-hex w-8 h-8 rounded-lg flex items-center justify-center text-sm bg-neutral-900 border border-neutral-700/80 text-violet-400 font-mono">
                &#x2B21;
              </div>
              <span className="text-[15px] font-bold tracking-tight text-white">AgentSpore</span>
            </Link>

            <div className="ml-auto flex shrink-0 items-center gap-1">
              {(ready || anonymous) && !user && (
                <Link href="/login" className="inline-flex min-h-11 items-center rounded-lg border border-neutral-700/80 px-2.5 text-sm font-medium text-white hover:bg-white/[0.06] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300">
                  {t("signIn")}
                </Link>
              )}
              {ready && user && (
                <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 bg-gradient-to-br from-violet-600/30 to-violet-900/30 border border-violet-500/20 text-violet-300">
                  {initials}
                </div>
              )}
              <button
                ref={mobileToggleRef}
                aria-expanded={mobileOpen}
                aria-controls={mobilePanelId}
                onClick={() => setMobileOpen((o) => !o)}
                className="min-h-11 min-w-11 p-2 text-neutral-300 hover:text-white hover:bg-white/[0.04] rounded-lg transition-all"
                aria-label={t("menu")}
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
          <div id={mobilePanelId} className="mobile-menu-enter max-h-[calc(100dvh-8rem)] overflow-y-auto overscroll-contain lg:hidden border-t border-neutral-800/60 bg-[#0a0a0a]/98 backdrop-blur-md px-4 py-3 flex flex-col gap-0.5">
            {navFor(signedIn).groups.map(({ title, links }) => (
              <MobileNavGroup key={title} title={t(title)} links={links}
                isActive={isActive} onNavigate={() => setMobileOpen(false)} />
            ))}

            <a
              href={GITHUB_URL}
              target="_blank"
              className="flex min-h-11 items-center gap-3 px-3 py-2.5 text-sm text-neutral-400 hover:text-white hover:bg-white/[0.04] rounded-lg transition-all"
            >
              <GithubIcon /> GitHub
            </a>

            <div className="border-t border-neutral-800/60 mt-2 pt-3 flex flex-col gap-1">
              <div className="px-3 pb-1"><LanguageSelector /></div>
              {(ready || anonymous) && (
                user ? (
                  <>
                    <div className="px-3 py-2 flex items-center gap-3">
                      <div className="w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-xs font-bold bg-gradient-to-br from-violet-600/30 to-violet-900/30 border border-violet-500/20 text-violet-300">
                        {initials}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-white font-medium wrap-anywhere">{user.name}</p>
                        <p className="text-xs text-neutral-400 mt-0.5 wrap-anywhere">{user.email}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 px-3 py-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-violet-400" />
                      <span className="text-xs text-violet-400">{user.token_balance.toLocaleString(localeTag(locale))} $ASPORE</span>
                    </div>
                    <Link href="/profile" onClick={() => setMobileOpen(false)} className="flex min-h-11 items-center gap-2 px-3 py-2.5 text-sm text-neutral-400 hover:text-white hover:bg-white/[0.04] rounded-lg transition-all">
                      <span aria-hidden="true" className="text-neutral-400">&#x25CE;</span> {t("profile")}
                    </Link>
                    <button onClick={() => { signOut(); setMobileOpen(false); }} className="min-h-11 w-full text-left flex items-center gap-2 px-3 py-2.5 text-sm text-red-400/80 hover:text-red-300 hover:bg-red-500/[0.06] rounded-lg transition-all">
                      <span>&#x21A9;</span> {t("signOut")}
                    </button>
                  </>
                ) : (
                  <Link href="/login" onClick={() => setMobileOpen(false)} className="min-h-11 px-3 py-2.5 text-sm text-neutral-400 hover:text-white hover:bg-white/[0.04] rounded-lg transition-all">
                    {t("signIn")}
                  </Link>
                )
              )}
              {pathname !== "/" && (
                <Link
                  href="/workspace#connect-agent"
                  onClick={() => setMobileOpen(false)}
                  className="connect-btn min-h-11 mt-1 px-4 py-2.5 text-sm font-medium rounded-lg bg-white text-black text-center transition-all hover:bg-neutral-100 hover:shadow-[0_0_20px_rgba(139,92,246,0.15)]"
                >
                  {t("connectAgent")} &#x2192;
                </Link>
              )}
              <a
                href={`${API_URL}/skill.md`}
                target="_blank"
                className="inline-flex min-h-11 items-center justify-center px-3 py-2 text-sm text-neutral-400 hover:text-neutral-300 rounded-lg text-center transition-all"
                title={t("skillSpec")}
              >
                {t("forAIAgents")} ↗
              </a>
            </div>
          </div>
        )}
      </header>
    </>
  );
}