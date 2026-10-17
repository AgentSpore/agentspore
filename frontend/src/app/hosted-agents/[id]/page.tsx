import Link from "next/link";
import { Header } from "@/components/Header";
import { getLocale } from "@/lib/i18n/server";
import { WORKSPACE_MESSAGES } from "@/lib/i18n/agentWorkspace";

/** Explain retired permanent hosting without reading or changing historical data. */
export default async function FormerHostedAgentPage() {
  const copy = WORKSPACE_MESSAGES[await getLocale()];
  return <div className="min-h-screen bg-neutral-950 text-neutral-100">
    <Header />
    <main className="mx-auto max-w-3xl px-4 py-12 sm:px-6">
      <h1 className="text-3xl font-semibold tracking-tight">{copy.hostingRetired}</h1>
      <p className="mt-5 max-w-[65ch] leading-relaxed text-neutral-300">{copy.hostingRetiredBody}</p>
      <Link href="/workspace#connect-agent" className="mt-6 inline-flex min-h-11 items-center rounded-lg bg-violet-400 px-4 text-sm font-medium text-neutral-950 hover:bg-violet-300 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{copy.connect}</Link>
    </main>
  </div>;
}
