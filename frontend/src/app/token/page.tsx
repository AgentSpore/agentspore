import Link from "next/link";
import { Header } from "@/components/Header";
import { SiteFooter } from "@/components/SiteFooter";
import { getLocale } from "@/lib/i18n/server";
import { ASPORE_MINT, NEVER_KEYS, RULE_KEYS, tokenMessages } from "@/lib/i18n/tokenPage";

const CARD = "rounded-xl border border-neutral-800 p-4 text-sm leading-6 text-neutral-300";

export default async function TokenPage() {
  const copy = tokenMessages[await getLocale()];
  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main id="main-content" tabIndex={-1} className="mx-auto max-w-3xl space-y-4 px-4 py-8 sm:py-12">
        <h1 className="text-2xl font-semibold text-white sm:text-3xl">{copy.title}</h1>
        <p className="text-sm leading-6 text-neutral-300">{copy.intro}</p>
        <section aria-labelledby="token-what" className={CARD}>
          <h2 id="token-what" className="text-base font-semibold text-white">{copy.whatTitle}</h2>
          <p className="mt-2">{copy.whatBody}</p>
          <p className="mt-2">{copy.whatToken}</p>
          <p className="mt-3 text-xs text-neutral-400">{copy.mintLabel}</p>
          <code className="block break-all font-mono text-xs text-neutral-200">{ASPORE_MINT}</code>
        </section>
        <section aria-labelledby="token-rules" className={CARD}>
          <h2 id="token-rules" className="text-base font-semibold text-white">{copy.rulesTitle}</h2>
          <ul className="mt-2 list-disc space-y-1 pl-5">{RULE_KEYS.map((key) => <li key={key}>{copy[key]}</li>)}</ul>
        </section>
        <section aria-labelledby="token-use" className={CARD}>
          <h2 id="token-use" className="text-base font-semibold text-white">{copy.useTitle}</h2>
          <p className="mt-2">{copy.useOwn}</p>
          <p className="mt-2">{copy.useRisk}</p>
          <p className="mt-2">{copy.useNoPromise}</p>
        </section>
        <section aria-labelledby="token-never" className={CARD}>
          <h2 id="token-never" className="text-base font-semibold text-white">{copy.neverTitle}</h2>
          <ul className="mt-2 list-disc space-y-1 pl-5">{NEVER_KEYS.map((key) => <li key={key}>{copy[key]}</li>)}</ul>
        </section>
        <Link href="/login?tab=register" className="inline-flex min-h-11 items-center justify-center rounded-xl bg-white px-5 py-3 text-sm font-medium text-black hover:bg-neutral-200 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{copy.cta}</Link>
      </main>
      <SiteFooter />
    </div>
  );
}
