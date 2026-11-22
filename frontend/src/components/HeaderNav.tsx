"use client";

import Link from "next/link";
import { useId, type RefObject } from "react";
import { API_URL } from "@/lib/api";
import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { NAVIGATION_MESSAGES } from "@/lib/i18n/navigation";

export const GITHUB_URL = "https://github.com/AgentSpore";

export function GithubIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.3 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 21.795 24 17.295 24 12c0-6.63-5.37-12-12-12" />
    </svg>
  );
}

export type NavLabel = keyof typeof NAVIGATION_MESSAGES.en;
export interface NavLink { href: string; label: NavLabel; }
export interface NavGroup { title: "peopleGroup" | "platformGroup" | "communityGroup"; links: NavLink[]; }

const link = (label: NavLabel, href = `/${label}`): NavLink => ({ href, label });
const showcase = link("showcase", "/showcase");

/** Anonymous visitors get public destinations only; Workspace and Dashboard need an account. */
export function navFor(signedIn: boolean): { primary: NavLink[]; more: NavLink[]; groups: NavGroup[] } {
  const community = [link("chat"), link("teams"), link("blog")];
  const people: NavGroup = { title: "peopleGroup", links: [showcase] };
  if (signedIn) {
    const platform = [link("workspace"), link("projects"), link("agents"), link("dashboard")];
    return {
      primary: [showcase, ...platform],
      more: [...community, link("token")],
      groups: [people, { title: "platformGroup", links: [...platform, link("token")] }, { title: "communityGroup", links: community }],
    };
  }
  const platform = [link("projects"), link("agents"), link("token")];
  return {
    primary: [showcase, link("projects"), link("agents"), link("blog"), link("token")],
    more: [link("chat"), link("teams")],
    groups: [people, { title: "platformGroup", links: platform }, { title: "communityGroup", links: community }],
  };
}

export function MobileNavGroup({ title, links, isActive, onNavigate }: {
  title: string; links: NavLink[]; isActive: (href: string) => boolean; onNavigate: () => void;
}) {
  const headingId = useId();
  const t = useTranslations(NAVIGATION_MESSAGES);
  return (
    <nav aria-labelledby={headingId} className="py-2">
      <h2 id={headingId} className="px-3 pb-1 text-xs font-medium text-neutral-300">{title}</h2>
      {links.map(({ href, label }) => (
        <Link key={href} href={href} onClick={onNavigate}
          aria-current={isActive(href) ? "page" : undefined}
          className={`flex min-h-11 items-center justify-between px-3 py-2.5 text-sm rounded-lg transition-all focus-visible:outline-2 focus-visible:outline-violet-300 ${
            isActive(href) ? "text-white bg-white/[0.06]" : "text-neutral-400 hover:text-white hover:bg-white/[0.04]"
          }`}>
          {t(label)}
          {isActive(href) && <span aria-hidden="true" className="w-1 h-1 rounded-full bg-violet-400" />}
        </Link>
      ))}
    </nav>
  );
}

const TAB = "relative flex min-h-16 items-center gap-2 border-b-2 px-2.5 text-[13px] font-medium whitespace-nowrap transition-all focus-visible:outline-2 focus-visible:outline-violet-300 xl:px-4";
const tabState = (active: boolean) =>
  active ? "text-white border-violet-500" : "text-neutral-300 border-transparent hover:text-neutral-300 hover:border-neutral-700";
const ITEM = "flex min-h-11 items-center gap-2.5 px-4 py-2 text-[13px] transition-all";

interface DesktopNavProps {
  signedIn: boolean; isActive: (href: string) => boolean;
  moreOpen: boolean; setMoreOpen: (open: boolean | ((open: boolean) => boolean)) => void;
  moreRef: RefObject<HTMLDivElement | null>;
}

/** Single-row primary tabs plus the "More" disclosure. */
export function DesktopNav({ signedIn, isActive, moreOpen, setMoreOpen, moreRef }: DesktopNavProps) {
  const t = useTranslations(NAVIGATION_MESSAGES);
  const morePanelId = useId();
  const { primary, more } = navFor(signedIn);
  return (
    <nav className="flex min-w-0 items-center">
      {primary.map(({ href, label }) => (
        <Link key={href} href={href} aria-current={isActive(href) ? "page" : undefined} className={`${TAB} ${tabState(isActive(href))}`}>
          {t(label)}
        </Link>
      ))}
      <div className="relative" ref={moreRef}>
        <button onClick={() => setMoreOpen((o) => !o)} aria-controls={morePanelId} aria-expanded={moreOpen}
          className={`${TAB} ${tabState(more.some((l) => isActive(l.href)))}`}>
          {t("more")}
          <svg className={`w-2.5 h-2.5 text-neutral-400 transition-transform ${moreOpen ? "rotate-180" : ""}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M3 5l3 3 3-3" />
          </svg>
        </button>
        {moreOpen && (
          <div id={morePanelId} className="header-menu-enter absolute left-1/2 -translate-x-1/2 top-full mt-2 w-48 rounded-xl border border-neutral-800/80 bg-[#0c0c0c] shadow-[0_8px_40px_rgba(0,0,0,0.6)] py-1 z-50">
            {more.map(({ href, label }) => (
              <Link key={href} href={href} aria-current={isActive(href) ? "page" : undefined} onClick={() => setMoreOpen(false)}
                className={`${ITEM} ${isActive(href) ? "text-white bg-white/[0.04]" : "text-neutral-400 hover:text-white hover:bg-white/[0.04]"}`}>
                {t(label)}
              </Link>
            ))}
            <div className="border-t border-neutral-800/40 mt-1 pt-1">
              <a href={GITHUB_URL} target="_blank" onClick={() => setMoreOpen(false)} className={`${ITEM} text-neutral-400 hover:text-white hover:bg-white/[0.04]`}>
                <span aria-hidden="true" className="w-4 text-center text-neutral-700"><GithubIcon /></span>
                GitHub
              </a>
              <a href={`${API_URL}/skill.md`} target="_blank" onClick={() => setMoreOpen(false)} title={t("skillSpec")}
                className={`${ITEM} text-neutral-400 hover:text-white hover:bg-white/[0.04]`}>
                <span aria-hidden="true" className="w-4 text-center text-neutral-500 text-xs">AI</span>
                {t("forAI")}
              </a>
            </div>
          </div>
        )}
      </div>
    </nav>
  );
}
