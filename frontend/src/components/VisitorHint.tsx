"use client";

import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";

/** Tells anonymous visitors which parts of the site are open without an account. */
export function VisitorHint() {
  const tr = useTranslations(docsMessages);
  return (
    <section aria-labelledby="visitor-hint" className="mx-auto my-8 max-w-3xl rounded-xl border border-neutral-800 bg-neutral-900/30 p-4 text-sm">
      <h2 id="visitor-hint" className="font-medium text-white">{tr("vTitle")}</h2>
      <p className="mt-1 leading-6 text-neutral-300">{tr("vBody")}</p>
    </section>
  );
}
