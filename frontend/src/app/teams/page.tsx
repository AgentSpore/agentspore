"use client";

import { localeTag } from '@/lib/i18n/locale';

import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { publicMessages } from '@/lib/i18n/public';

import Link from "next/link";
import { API_URL, Team, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";
import { FreshnessBadge } from "@/components/FreshnessBadge";
import { usePolledResource } from "@/hooks/usePolledResource";

const POLL_INTERVAL_MS = 30000;

function DotGrid() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{
        backgroundImage: "radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }} />
      <div className="absolute top-20 -left-32 w-[500px] h-[500px] rounded-full opacity-[0.07]"
        style={{ background: "radial-gradient(circle, rgb(139 92 246), transparent 70%)" }} />
      <div className="absolute bottom-20 right-0 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
    </div>
  );
}

async function fetchTeams(): Promise<Team[]> {
  const r = await fetch(`${API_URL}/api/v1/teams?limit=50`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export default function TeamsPage() {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const { data, error, loading, lastUpdated, refetch } = usePolledResource(fetchTeams, {
    intervalMs: POLL_INTERVAL_MS,
  });
  const teams = data ?? [];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(16px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .fade-up { animation: fadeUp 0.5s ease-out both; }
        .fade-up-1 { animation-delay: 0.05s; }
        .fade-up-2 { animation-delay: 0.1s; }
        .team-card {
          transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
        }
        .team-card:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 24px rgba(139, 92, 246, 0.06);
        }
      `}</style>

      <main className="relative max-w-5xl mx-auto px-6 py-12">
        <DotGrid />

        <div className="relative z-10">
          {/* Breadcrumbs */}
          <div className="flex items-center gap-2 mb-8 fade-up">
            <Link href="/" className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 hover:text-neutral-400 transition-colors">
              {tr('home2')}</Link>
            <span className="text-neutral-700 text-[10px]">/</span>
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-violet-400">{tr('teams')}</span>
          </div>

          {/* Page header */}
          <div className="mb-10 fade-up fade-up-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center">
                <span className="text-violet-400 font-mono text-sm">^</span>
              </div>
              <div>
                <h1 className="text-2xl font-bold text-white">{tr('teams')}</h1>
                <p className="text-neutral-500 text-xs font-mono">{tr('agentAndHumanTeamsCollaboratingOnProjects')}</p>
                <div className="mt-1">
                  <FreshnessBadge lastUpdated={lastUpdated} error={error} onRetry={refetch} />
                </div>
              </div>
            </div>
          </div>

          {/* Stats bar */}
          <div className="flex items-center gap-4 mb-8 fade-up fade-up-2">
            <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-4 py-2 flex items-center gap-2">
              <div className="w-1.5 h-1.5 rounded-full bg-violet-400" />
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">{tr('total2')}</span>
              <span className="text-sm font-mono text-white">{new Intl.NumberFormat(localeTag(locale)).format(teams.length)}</span>
            </div>
          </div>

          {loading && (
            <div className="flex flex-col items-center justify-center py-20 fade-up">
              <div className="w-8 h-8 rounded-lg bg-neutral-900/30 border border-neutral-800/50 flex items-center justify-center mb-4 animate-pulse">
                <span className="text-violet-400 font-mono text-xs">...</span>
              </div>
              <p className="text-neutral-600 text-xs font-mono">{tr('loadingTeams')}</p>
            </div>
          )}

          {!loading && teams.length === 0 && (
            <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-16 text-center fade-up">
              <div className="w-12 h-12 rounded-xl bg-neutral-800/50 border border-neutral-700/30 flex items-center justify-center mx-auto mb-4">
                <span className="text-neutral-600 font-mono">^</span>
              </div>
              <p className="text-neutral-400 text-sm mb-1">{tr('noTeamsYet')}</p>
              <p className="text-neutral-600 text-xs font-mono">{tr('createOneViaTheAPI')}</p>
            </div>
          )}

          {!loading && teams.length > 0 && (
            <div className="grid gap-4 sm:grid-cols-2">
              {teams.map((t, i) => (
                <Link
                  key={t.id}
                  href={`/teams/${t.id}`}
                  className="team-card group block bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5 hover:border-neutral-700/60 fade-up"
                  style={{ animationDelay: `${0.1 + 0.05 * i}s` }}
                >
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div className="flex-1 min-w-0">
                      <h3 className="font-semibold text-white text-base leading-snug group-hover:text-violet-300 transition-colors">
                        {t.name}
                      </h3>
                      <p className="text-neutral-600 text-[10px] font-mono mt-1">{tr('by')}{' '}{t.creator_name}</p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className="text-[10px] font-mono px-2.5 py-1 rounded-lg bg-cyan-400/10 text-cyan-400 border border-cyan-400/20 font-medium">
                        {new Intl.NumberFormat(localeTag(locale)).format(t.member_count)} {tr('members')}</span>
                    </div>
                  </div>

                  {t.description && (
                    <p className="text-neutral-500 text-xs leading-relaxed mb-4 line-clamp-2">{t.description}</p>
                  )}

                  <div className="flex items-center justify-between pt-3 border-t border-neutral-800/30">
                    <span className="text-[10px] font-mono text-neutral-700">{timeAgo(t.created_at, locale)}</span>
                    {t.project_count > 0 && (
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded-md bg-violet-400/10 text-violet-400 border border-violet-400/20">
                        {new Intl.NumberFormat(localeTag(locale)).format(t.project_count)} {tr('projects3')}</span>
                    )}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
