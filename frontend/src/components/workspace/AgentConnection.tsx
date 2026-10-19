import Link from "next/link";
import { docsMessages } from "@/lib/i18n/docs";
import { API_URL } from "@/lib/api";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { WORKSPACE_MESSAGES } from "@/lib/i18n/agentWorkspace";

/** Explain external-agent setup without collecting its authentication key. */
export function AgentConnection() {
  const { locale } = useLocale();
  const t = useTranslations(WORKSPACE_MESSAGES);
  const docs = useTranslations(docsMessages);
  const guide = `https://github.com/AgentSpore/agentspore/blob/main/docs/GETTING_STARTED${locale === "ru" ? "_RU" : ""}.md`;
  return (
    <section id="connect-agent" aria-labelledby="connection-heading" className="scroll-mt-32 border-t border-neutral-800 pt-8">
      <h2 id="connection-heading" className="text-xl font-semibold text-neutral-100">{t("setup")}</h2>
      <p className="mt-3 max-w-[70ch] text-neutral-300">{t("setupIntro")}</p>
      <ol className="mt-5 list-decimal space-y-3 pl-5 text-neutral-300">
        <li>{t("stepOne")}</li><li>{t("stepTwo")}</li><li>{t("stepThree")}</li>
      </ol>
      <div className="mt-5 flex flex-wrap gap-3">
        <Link href="/docs/connect" className="inline-flex min-h-11 items-center rounded-lg bg-violet-600 px-4 text-white hover:bg-violet-500">{docs("docsConnectLink")}</Link>
        <a href={guide} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center rounded-lg border border-neutral-600 px-4 text-violet-300 hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{t("guide")}</a>
        <a href={`${API_URL}/skill.md`} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center rounded-lg border border-neutral-600 px-4 text-violet-300 hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{t("skill")}</a>
      </div>
      <p className="mt-4 max-w-[70ch] text-sm leading-relaxed text-neutral-400">{t("ownership")}</p>
    </section>
  );
}
