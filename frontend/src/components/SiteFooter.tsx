import Link from "next/link";
import { getLocale } from "@/lib/i18n/server";
import { docsMessages } from "@/lib/i18n/docs";

const LINK = "inline-flex min-h-11 min-w-11 items-center justify-center px-2 text-xs text-neutral-300 hover:text-white transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400";

/** Trust and help links shared by the pages that render a footer. */
export async function SiteFooter() {
  const copy = docsMessages[await getLocale()];
  return (
    <footer className="border-t border-neutral-800/80 px-4 py-5 sm:px-6">
      <nav aria-label={copy.footerNavigation} className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 sm:gap-4">
        <Link href="/docs/connect" className={LINK}>{copy.fConnect}</Link>
        <Link href="/docs/glossary" className={LINK}>{copy.fGlossary}</Link>
        <a href="https://github.com/AgentSpore/agentspore" className={LINK}>{copy.fRepo}</a>
        <Link href="/privacy" className={LINK}>{copy.fPrivacy}</Link>
      </nav>
    </footer>
  );
}
