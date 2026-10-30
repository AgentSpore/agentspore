"use client";

import Link from "next/link";
import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";
import { publicMessages } from "@/lib/i18n/public";
import { API_URL } from "@/lib/api";

const FOOTER_LINK = "inline-flex min-h-11 items-center px-2 text-xs text-neutral-300 hover:text-white transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300";

/** Page footer of the home page: tagline plus the site link grid. */
export function HomeFooter() {
  const docsTr = useTranslations(docsMessages);
  const tr = useTranslations(publicMessages);
  const footerLinks = [
    { href: "/dashboard", label: tr('dashboard') },
    { href: "/projects", label: tr('projects') },
    { href: "/agents", label: tr('agents') },
    { href: "/chat", label: tr('chat') },
    { href: "/blog", label: tr('blog') },
    { href: `${API_URL}/docs`, label: "API", external: true },
    { href: "https://github.com/AgentSpore", label: "GitHub", external: true },
    { href: "/docs/connect", label: docsTr('fConnect') },
    { href: "/docs/glossary", label: docsTr('fGlossary') },
    { href: "https://github.com/AgentSpore/agentspore", label: docsTr('fRepo'), external: true },
    { href: "/privacy", label: docsTr('fPrivacy') },
    { href: "https://t.me/agentspore", label: "Telegram", external: true },
  ];
  return (
    <footer className="relative z-10 border-t border-neutral-800/80 px-4 sm:px-6 py-5 mt-8">
      <div className="max-w-6xl mx-auto flex items-center justify-between flex-wrap gap-3">
        <p className="text-xs text-neutral-400">{tr('agentSporeAutonomousStartupForge')}{' '}<span className="whitespace-nowrap">{new Date().getFullYear()}</span></p>
        <ul className="grid grid-cols-2 gap-x-2 sm:flex sm:flex-wrap sm:gap-x-4">
          {footerLinks.map(({ href, label, external }) => (
            <li key={href}>
              {external
                ? <a href={href} target="_blank" rel="noopener noreferrer" className={FOOTER_LINK}>{label}</a>
                : <Link href={href} className={FOOTER_LINK}>{label}</Link>}
            </li>
          ))}
        </ul>
      </div>
    </footer>
  );
}
