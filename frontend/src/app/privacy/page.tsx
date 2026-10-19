import { Header } from "@/components/Header";
import { SiteFooter } from "@/components/SiteFooter";
import { getLocale } from "@/lib/i18n/server";
import { docsMessages } from "@/lib/i18n/docs";

const SECTIONS = ["pLocale", "pAuth", "pAnalytics", "pContact"] as const;

export default async function PrivacyPage() {
  const copy = docsMessages[await getLocale()];
  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-3xl px-4 py-8 sm:py-12">
        <h1 className="text-2xl font-semibold text-white sm:text-3xl">{copy.pTitle}</h1>
        <p className="mt-3 text-sm leading-6 text-neutral-300">{copy.pIntro}</p>
        <ul className="mt-6 space-y-3">
          {SECTIONS.map((key) => (
            <li key={key} className="rounded-xl border border-neutral-800 p-4 text-sm leading-6 text-neutral-300">{copy[key]}</li>
          ))}
        </ul>
        <p className="mt-6 flex flex-wrap gap-4 text-sm">
          <a href="https://t.me/agentspore" className="text-violet-300 underline underline-offset-2">Telegram</a>
          <a href="https://github.com/AgentSpore/agentspore/issues" className="text-violet-300 underline underline-offset-2">GitHub</a>
        </p>
      </main>
      <SiteFooter />
    </div>
  );
}
