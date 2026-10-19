import { Header } from "@/components/Header";
import { SiteFooter } from "@/components/SiteFooter";
import { getLocale } from "@/lib/i18n/server";
import { docsMessages, GLOSSARY_TERMS } from "@/lib/i18n/docs";

export default async function GlossaryPage() {
  const copy = docsMessages[await getLocale()];
  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main id="main-content" tabIndex={-1} className="mx-auto max-w-3xl px-4 py-8 sm:py-12">
        <h1 className="text-2xl font-semibold text-white sm:text-3xl">{copy.gTitle}</h1>
        <p className="mt-3 text-sm leading-6 text-neutral-300">{copy.gIntro}</p>
        <dl className="mt-8 space-y-6">
          {GLOSSARY_TERMS.map((term) => (
            <div key={term} id={term} className="scroll-mt-20 rounded-xl border border-neutral-800 p-4 target:border-violet-500">
              <dt className="text-base font-semibold text-white">{copy[`t_${term}`]}</dt>
              <dd className="mt-1 text-sm leading-6 text-neutral-300">{copy[`d_${term}`]}</dd>
            </div>
          ))}
        </dl>
      </main>
      <SiteFooter />
    </div>
  );
}
