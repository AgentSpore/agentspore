"use client";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import { Header } from "@/components/Header";
import { FetchError } from "@/components/FetchError";
import { SkeletonText } from "@/components/Skeleton";
import { API_URL } from "@/lib/api";
import { fetchWithAuth, getAuthSnapshot, getServerAuthSnapshot, subscribeAuthChanges } from "@/lib/auth";
import { buildFunnelTable, type FunnelReport, type FunnelTable } from "@/lib/funnel/report";
import { localeTag } from "@/lib/i18n/locale";
import { funnelMessages } from "@/lib/i18n/funnel";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";

const PERIODS = [7, 30, 90] as const;
type Period = (typeof PERIODS)[number];
type LoadState =
  | { kind: "loading" } | { kind: "error" } | { kind: "auth" } | { kind: "forbidden" }
  | { kind: "ready"; table: FunnelTable };

function useFunnel(days: Period, token: string | null, initializing: boolean, revision: number): LoadState {
  const [stored, setStored] = useState<{ key: string; state: LoadState } | null>(null);
  const key = `${days}:${token}:${revision}`;
  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    const save = (state: LoadState) => { if (!controller.signal.aborted) setStored({ key, state }); };
    void (async () => {
      try {
        const res = await fetchWithAuth(`${API_URL}/api/v1/admin/funnel?days=${days}`, { signal: controller.signal });
        if (res.status === 401) return save({ kind: "auth" });
        if (res.status === 403) return save({ kind: "forbidden" });
        if (!res.ok) return save({ kind: "error" });
        save({ kind: "ready", table: buildFunnelTable((await res.json()) as FunnelReport) });
      } catch {
        save({ kind: "error" });
      }
    })();
    return () => controller.abort();
  }, [days, token, key]);
  if (!token) return initializing ? { kind: "loading" } : { kind: "auth" };
  return stored?.key === key ? stored.state : { kind: "loading" };
}

export default function AdminFunnelPage() {
  const auth = useSyncExternalStore(subscribeAuthChanges, getAuthSnapshot, getServerAuthSnapshot);
  const tr = useTranslations(funnelMessages);
  const { locale } = useLocale();
  const [days, setDays] = useState<Period>(7);
  const [revision, setRevision] = useState(0);
  const state = useFunnel(days, auth.accessToken, auth.generation < 0, revision);
  const percent = new Intl.NumberFormat(localeTag(locale), { style: "percent", maximumFractionDigits: 1 });
  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main id="main-content" tabIndex={-1} className="mx-auto max-w-5xl px-4 py-8 sm:py-12">
        <h1 className="text-2xl font-semibold text-white sm:text-3xl">{tr("title")}</h1>
        <p className="mt-2 text-sm text-neutral-300">{tr("intro")}</p>
        <div role="group" aria-label={tr("period")} className="mt-6 flex gap-2">
          {PERIODS.map(value => (
            <button key={value} type="button" aria-pressed={days === value} onClick={() => setDays(value)}
              className={`min-h-11 rounded-lg border px-4 text-sm ${days === value ? "border-violet-400 bg-violet-600 text-white" : "border-neutral-700 text-neutral-300 hover:bg-neutral-900"}`}>
              {tr(`days${value}`)}
            </button>
          ))}
        </div>
        <div className="mt-6" aria-live="polite">
          {state.kind === "loading" && <div role="status" aria-label={tr("loading")}><SkeletonText lines={6} /></div>}
          {state.kind === "error" && <FetchError onRetry={() => setRevision(value => value + 1)} />}
          {state.kind === "auth" && (
            <Notice title={tr("authTitle")} body={tr("authBody")}>
              <Link href="/login" className="mt-3 inline-flex min-h-11 items-center text-violet-300 underline underline-offset-2">{tr("signIn")}</Link>
            </Notice>
          )}
          {state.kind === "forbidden" && <Notice title={tr("forbiddenTitle")} body={tr("forbiddenBody")} />}
          {state.kind === "ready" && (state.table.days.length === 0
            ? <Notice title={tr("emptyTitle")} body={tr("emptyBody")} />
            : <FunnelGrid table={state.table} percent={percent} />)}
        </div>
      </main>
    </div>
  );
}

function Notice({ title, body, children }: { title: string; body: string; children?: React.ReactNode }) {
  return (
    <div role="alert" className="rounded-xl border border-neutral-800 p-6 text-center">
      <h2 className="text-base font-semibold text-white">{title}</h2>
      <p className="mt-2 text-sm text-neutral-300">{body}</p>
      {children}
    </div>
  );
}

function FunnelGrid({ table, percent }: { table: FunnelTable; percent: Intl.NumberFormat }) {
  const tr = useTranslations(funnelMessages);
  const label = (step: string) => (`step_${step}` in funnelMessages.en ? tr(`step_${step}` as keyof typeof funnelMessages.en) : step);
  const rate = (step: string) => {
    const value = table.conversions[step];
    return value === null || value === undefined ? tr("noData") : percent.format(value);
  };
  return (
    <div className="overflow-x-auto rounded-xl border border-neutral-800">
      <table className="w-full text-left text-sm">
        <thead className="bg-neutral-900 text-neutral-300">
          <tr>
            <th scope="col" className="px-3 py-2">{tr("day")}</th>
            {table.steps.map(step => <th key={step} scope="col" className="px-3 py-2 text-right">{label(step)}</th>)}
          </tr>
        </thead>
        <tbody>
          {table.days.map(row => (
            <tr key={row.day} className="border-t border-neutral-800">
              <th scope="row" className="px-3 py-2 font-normal">{row.day}</th>
              {table.steps.map(step => <td key={step} className="px-3 py-2 text-right tabular-nums">{row.counts[step] ?? 0}</td>)}
            </tr>
          ))}
        </tbody>
        <tfoot className="border-t border-neutral-700 font-medium">
          <tr>
            <th scope="row" className="px-3 py-2">{tr("total")}</th>
            {table.steps.map(step => <td key={step} className="px-3 py-2 text-right tabular-nums">{table.totals[step]}</td>)}
          </tr>
          <tr className="text-neutral-300">
            <th scope="row" className="px-3 py-2">{tr("conversion")}</th>
            {table.steps.map(step => <td key={step} className="px-3 py-2 text-right tabular-nums">{rate(step)}</td>)}
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
