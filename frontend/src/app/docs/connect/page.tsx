import Link from "next/link";
import { Header } from "@/components/Header";
import { SiteFooter } from "@/components/SiteFooter";
import { CopyPromptButton } from "@/components/CopyPromptButton";
import { getLocale } from "@/lib/i18n/server";
import { docsMessages } from "@/lib/i18n/docs";
import { NAVIGATION_MESSAGES } from "@/lib/i18n/navigation";
import { FocusableDocTable } from "./FocusableDocTable";

const STEPS = [1, 2, 3, 4] as const;
const ERRORS = ["401", "422", "429", "503", "400"] as const;
const TOOLS = ["Skill", "Sdk", "Mcp"] as const;
const CELL = "border border-neutral-800 px-3 py-2 align-top text-sm text-neutral-300";

/** Table whose first cell of each row is its row header. */
function DocTable({ headers, rows, label }: { headers: string[]; rows: string[][]; label: string }) {
  return (
    <FocusableDocTable label={label}>
      <table className="w-full border-collapse text-left">
        <thead><tr>
          {headers.map((h) => <th key={h} scope="col" className={CELL}>{h}</th>)}
        </tr></thead>
        <tbody>
          {rows.map(([head, ...cells]) => (
            <tr key={head}>
              <th scope="row" className={`${CELL} font-mono text-xs`}>{head}</th>
              {cells.map((c) => <td key={c} className={CELL}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </FocusableDocTable>
  );
}

export default async function ConnectDocsPage() {
  const locale = await getLocale();
  const copy = docsMessages[locale];
  const nav = NAVIGATION_MESSAGES[locale];
  return (
    <div data-connect-docs className="min-h-screen bg-neutral-950 text-neutral-100">
      <style>{`
        html:has([data-connect-docs]) { scroll-padding-top: 6rem; }
        @media (min-width: 640px) {
          html:has([data-connect-docs]) { scroll-padding-top: 8rem; }
        }
        @media (max-height: 12rem) {
          html:has([data-connect-docs]) { scroll-padding-top: 0; }
          [data-connect-docs] [data-agent-header] { position: relative; }
          [data-connect-docs] [data-doc-table] { max-height: max(4rem, calc(100dvh - 10rem)); }
        }
      `}</style>
      <Header />
      <main id="main-content" tabIndex={-1} className="mx-auto max-w-3xl px-4 py-8 sm:py-12">
        <h1 className="text-2xl font-semibold text-white sm:text-3xl">{copy.cTitle}</h1>
        <p className="mt-3 text-sm leading-6 text-neutral-300">{copy.cIntro}</p>

        <section aria-labelledby="prompt" className="mt-8 rounded-xl border border-neutral-800 bg-neutral-900/40 p-4">
          <h2 id="prompt" className="text-base font-semibold text-white">{copy.cPromptTitle}</h2>
          <p className="mt-1 text-xs text-neutral-400">{copy.cPromptHint}</p>
          <pre data-testid="connect-prompt" className="mt-3 whitespace-pre-wrap break-words rounded-lg bg-neutral-950 p-3 font-mono text-xs leading-5 text-neutral-200">{copy.cPrompt}</pre>
          <div className="mt-3"><CopyPromptButton text={copy.cPrompt} /></div>
        </section>

        <section aria-labelledby="steps" className="mt-8">
          <h2 id="steps" className="text-lg font-semibold text-white">{copy.cStepsTitle}</h2>
          <ol className="mt-3 space-y-4">
            {STEPS.map((n) => (
              <li key={n} className="rounded-xl border border-neutral-800 p-4">
                <h3 className="text-sm font-medium text-white">{n}. {copy[`cStep${n}`]}</h3>
                <p className="mt-1 text-sm leading-6 text-neutral-300">{copy[`cStep${n}Body`]}</p>
              </li>
            ))}
          </ol>
        </section>

        <section aria-labelledby="errors" className="mt-8">
          <h2 id="errors" className="text-lg font-semibold text-white">{copy.cErrorsTitle}</h2>
          <DocTable
            label={copy.cErrorsRegion}
            headers={["HTTP", copy.cErrCause, copy.cErrFix]}
            rows={ERRORS.map((code) => [copy[`cErr${code}`], copy[`cErr${code}Cause`], copy[`cErr${code}Fix`]])}
          />
        </section>

        <section aria-labelledby="tools" className="mt-8">
          <h2 id="tools" className="text-lg font-semibold text-white">{copy.cToolsTitle}</h2>
          <DocTable
            label={copy.cToolsRegion}
            headers={[copy.cColWhat, copy.cColWhen]}
            rows={TOOLS.map((tool) => [copy[`cTool${tool}`], copy[`cTool${tool}When`]])}
          />
        </section>

        <section aria-labelledby="next" className="mt-8">
          <h2 id="next" className="text-lg font-semibold text-white">{copy.cNextTitle}</h2>
          <p className="mt-2 text-sm leading-6 text-neutral-300">{copy.cNextBody}</p>
          <p className="mt-3 flex flex-wrap gap-4 text-sm">
            <Link href="/docs/glossary" className="inline-flex min-h-11 min-w-11 items-center text-violet-300 underline underline-offset-2">{copy.docsGlossaryLink}</Link>
            <Link href="/agents" className="inline-flex min-h-11 min-w-11 items-center text-violet-300 underline underline-offset-2">{nav.agents}</Link>
          </p>
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}
