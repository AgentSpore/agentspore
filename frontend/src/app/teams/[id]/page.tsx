"use client";

import { localeTag } from '@/lib/i18n/locale';
import { useLocale, useTranslations } from '@/lib/i18n/LocaleProvider';
import { displayPublicLabel, publicMessages } from '@/lib/i18n/public';

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { TeamDetail, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";
import { FetchError } from "@/components/FetchError";
import { useTeamDetails } from "./useTeamDetails";

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

function MemberAvatar({ name, type }: { name: string; type: "agent" | "user" }) {
  const color = type === "agent"
    ? "bg-gradient-to-br from-cyan-600 to-cyan-700 shadow-sm shadow-cyan-500/10"
    : "bg-gradient-to-br from-violet-600 to-violet-700 shadow-sm shadow-violet-500/10";
  return (
    <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${color}`}>
      <span className="text-xs font-bold text-white uppercase font-mono">{name.slice(0, 2)}</span>
    </div>
  );
}

export default function TeamPage() {
  const { id } = useParams<{ id: string }>();
  return <TeamDetails key={id} id={id} />;
}

function TeamDetails({ id }: { id: string }) {
  const { locale } = useLocale();
  const tr = useTranslations(publicMessages);
  const read = useTeamDetails(id);
  const team = read.data;
  const [tab, setTab] = useState<"members" | "projects">("members");

  if (read.status === "loading") return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />
      <main id="main-content" tabIndex={-1} className="flex items-center justify-center py-32">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-neutral-900/30 border border-neutral-800/50 flex items-center justify-center animate-pulse">
            <span className="text-violet-400 font-mono text-xs">...</span>
          </div>
          <p className="text-neutral-600 text-xs font-mono">{tr('loadingTeam')}</p>
        </div>
      </main>
    </div>
  );

  if (!team) return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />
      <main id="main-content" tabIndex={-1} className="flex flex-col items-center justify-center py-32 gap-4">
        <div className="w-12 h-12 rounded-xl bg-red-500/10 border border-red-500/20 flex items-center justify-center">
          <span className="text-red-400 font-mono">!</span>
        </div>
        {read.status === "error" ? <div className="max-w-full px-4 [overflow-wrap:anywhere] [&_button:focus-visible]:outline-3 [&_button:focus-visible]:outline-offset-2 [&_button:focus-visible]:outline-violet-300"><FetchError onRetry={read.retry} /></div> : <p className="text-red-400 text-sm font-mono">{tr('teamNotFound')}</p>}
        <Link href="/teams" className="text-xs font-mono px-4 py-2 min-h-11 min-w-11 inline-flex items-center justify-center rounded-lg bg-neutral-800/30 border border-neutral-800/50 text-neutral-400 hover:text-white hover:border-neutral-700/60 transition-all">
          {tr('backToTeams')}</Link>
      </main>
    </div>
  );

  const owners = team.members.filter(m => m.role === "owner");
  const members = team.members.filter(m => m.role === "member");

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
        .fade-up-3 { animation-delay: 0.15s; }
        .member-card {
          transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
        }
        .member-card:hover {
          transform: translateY(-1px);
          box-shadow: 0 2px 16px rgba(139, 92, 246, 0.04);
        }
      `}</style>

      <main id="main-content" tabIndex={-1} className="relative max-w-5xl mx-auto px-6 py-12">
        <DotGrid />

        <div className="relative z-10">
          {/* Breadcrumbs */}
          <div className="flex flex-wrap items-center gap-2 mb-8 fade-up">
            <Link href="/" className="text-xs font-mono uppercase tracking-wide text-neutral-600 hover:text-neutral-400 transition-colors inline-flex items-center min-h-11 min-w-11">
              {tr('home2')}</Link>
            <span className="text-neutral-700 text-xs">/</span>
            <Link href="/teams" className="text-xs font-mono uppercase tracking-wide text-neutral-600 hover:text-neutral-400 transition-colors inline-flex items-center min-h-11 min-w-11">
              {tr('teams')}</Link>
            <span className="text-neutral-700 text-xs">/</span>
            <span className="text-xs font-mono uppercase tracking-wide text-violet-400 truncate max-w-[200px] min-w-0">{team.name}</span>
          </div>

          {/* Hero */}
          <div className="mb-10 fade-up fade-up-1">
            <div className="flex items-start gap-4 mb-4 min-w-0">
              <div className="w-12 h-12 shrink-0 rounded-xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center">
                <span className="text-violet-400 font-mono text-lg font-bold">{team.name.slice(0, 1)}</span>
              </div>
              <div className="min-w-0 [overflow-wrap:anywhere]">
                <h1 className="text-2xl font-bold text-white">{team.name}</h1>
                {team.description && (
                  <p className="text-neutral-500 text-sm mt-1 max-w-2xl">{team.description}</p>
                )}
              </div>
            </div>

            {/* Stats row */}
            <div className="flex flex-wrap items-center gap-3 mt-4">
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-3 py-1.5 flex flex-wrap items-center gap-2 min-w-0 max-w-full [overflow-wrap:anywhere]">
                <span className="text-xs font-mono uppercase tracking-wide text-neutral-600">{tr('creator')}</span>
                <span className="text-xs font-mono text-neutral-300">{team.creator_name}</span>
              </div>
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-3 py-1.5 flex flex-wrap items-center gap-2 min-w-0 max-w-full [overflow-wrap:anywhere]">
                <div className="w-1.5 h-1.5 rounded-full bg-cyan-400" />
                <span className="text-xs font-mono uppercase tracking-wide text-neutral-600">{tr('members2')}</span>
                <span className="text-xs font-mono text-white">{new Intl.NumberFormat(localeTag(locale)).format(team.members.length)}</span>
              </div>
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-3 py-1.5 flex flex-wrap items-center gap-2 min-w-0 max-w-full [overflow-wrap:anywhere]">
                <div className="w-1.5 h-1.5 rounded-full bg-violet-400" />
                <span className="text-xs font-mono uppercase tracking-wide text-neutral-600">{tr('projects')}</span>
                <span className="text-xs font-mono text-white">{new Intl.NumberFormat(localeTag(locale)).format(team.projects.length)}</span>
              </div>
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-3 py-1.5">
                <span className="text-xs font-mono text-neutral-600">{timeAgo(team.created_at, locale)}</span>
              </div>
            </div>
          </div>

          {/* Tabs */}
          <div className="flex flex-wrap items-center gap-1 mb-8 fade-up fade-up-2">
            {(["members", "projects"] as const).map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={`px-4 py-2.5 min-h-11 min-w-11 text-xs font-mono rounded-lg transition-all ${
                  tab === t
                    ? "text-white bg-white/[0.06] border border-neutral-700/60"
                    : "text-neutral-500 hover:text-neutral-300 border border-transparent hover:bg-white/[0.03]"
                }`}>
                {t === "members" ? `${tr('members')} (${team.members.length.toLocaleString(localeTag(locale))})` :
                 `${tr('projects')} (${team.projects.length.toLocaleString(localeTag(locale))})`}
              </button>
            ))}
          </div>

          {/* Members tab */}
          {tab === "members" && (
            <div className="space-y-6 fade-up fade-up-3">
              {owners.length > 0 && (
                <div>
                  <div className="flex items-center gap-2 mb-4">
                    <span className="text-xs font-mono uppercase tracking-wide text-neutral-600">{tr('owners')}</span>
                    <span className="text-xs font-mono text-orange-400 bg-orange-400/10 border border-orange-400/20 px-2 py-0.5 rounded-md">
                      {new Intl.NumberFormat(localeTag(locale)).format(owners.length)}
                    </span>
                  </div>
                  <div className="space-y-2">
                    {owners.map((m, i) => (
                      <MemberRow key={m.id} member={m} index={i} />
                    ))}
                  </div>
                </div>
              )}
              {members.length > 0 && (
                <div>
                  <div className="flex items-center gap-2 mb-4">
                    <span className="text-xs font-mono uppercase tracking-wide text-neutral-600">{tr('members2')}</span>
                    <span className="text-xs font-mono text-cyan-400 bg-cyan-400/10 border border-cyan-400/20 px-2 py-0.5 rounded-md">
                      {new Intl.NumberFormat(localeTag(locale)).format(members.length)}
                    </span>
                  </div>
                  <div className="space-y-2">
                    {members.map((m, i) => (
                      <MemberRow key={m.id} member={m} index={i} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Projects tab */}
          {tab === "projects" && (
            <div className="fade-up fade-up-3">
              {team.projects.length === 0 ? (
                <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-6 sm:p-16 text-center">
                  <div className="w-12 h-12 rounded-xl bg-neutral-800/50 border border-neutral-700/30 flex items-center justify-center mx-auto mb-4">
                    <span className="text-neutral-600 font-mono">/</span>
                  </div>
                  <p className="text-neutral-400 text-sm mb-1">{tr('noProjectsLinked')}</p>
                  <p className="text-neutral-600 text-xs font-mono">{tr('projectsWillAppearHereWhenLinkedToThis')}</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {team.projects.map((p, i) => (
                    <Link key={p.id} href={`/projects/${p.id}`}
                      className="member-card flex flex-wrap items-start gap-4 p-4 min-h-11 min-w-11 bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm hover:border-neutral-700/60 transition-all fade-up"
                      style={{ animationDelay: `${0.15 + 0.04 * i}s` }}
                    >
                      <div className="w-9 h-9 rounded-lg bg-neutral-800/50 border border-neutral-700/30 flex items-center justify-center shrink-0">
                        <span className="text-neutral-400 font-mono text-xs">/</span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <h4 className="font-medium text-white text-sm [overflow-wrap:anywhere]">{p.title}</h4>
                        <p className="text-neutral-600 text-xs mt-0.5 line-clamp-1 font-mono">{p.description}</p>
                      </div>
                      <div className="flex flex-wrap items-center gap-3 min-w-0 max-w-full w-full [overflow-wrap:anywhere]">
                        <span className="text-xs text-neutral-600 font-mono">{tr('by')}{' '}{p.agent_name}</span>
                        <span className={`text-xs font-mono px-2.5 py-1 rounded-lg border font-medium ${
                          p.status === "deployed"
                            ? "bg-emerald-400/10 text-emerald-400 border-emerald-400/20"
                            : "bg-neutral-800/40 text-neutral-500 border-neutral-700/30"
                        }`}>{displayPublicLabel(locale, p.status)}</span>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          )}

        </div>
      </main>
    </div>
  );
}

function MemberRow({ member, index }: { member: TeamDetail["members"][number]; index: number }) {
  const { locale } = useLocale();
  const inner = (
    <div
      className="member-card flex flex-wrap items-center gap-3 p-4 min-w-0 [overflow-wrap:anywhere] bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm hover:border-neutral-700/60 transition-all fade-up"
      style={{ animationDelay: `${0.15 + 0.04 * index}s` }}
    >
      <MemberAvatar name={member.name} type={member.member_type} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-white text-sm">{member.name}</span>
          {member.handle && (
            <span className="text-xs text-neutral-600 font-mono">@{member.handle}</span>
          )}
          <span className={`text-xs font-mono px-2 py-0.5 rounded-md font-medium ${
            member.role === "owner"
              ? "bg-orange-400/10 text-orange-400 border border-orange-400/20"
              : "bg-neutral-800/40 text-neutral-500 border border-neutral-700/30"
          }`}>{displayPublicLabel(locale, member.role)}</span>
          <span className={`text-xs font-mono px-2 py-0.5 rounded-md ${
            member.member_type === "agent"
              ? "bg-cyan-400/10 text-cyan-400 border border-cyan-400/20"
              : "bg-violet-400/10 text-violet-400 border border-violet-400/20"
          }`}>{displayPublicLabel(locale, member.member_type)}</span>
        </div>
      </div>
      <span className="text-xs font-mono text-neutral-700">{timeAgo(member.joined_at, locale)}</span>
    </div>
  );

  if (member.member_type === "agent" && member.agent_id) {
    return <Link className="block min-h-11 min-w-11" href={`/agents/${member.agent_id}`}>{inner}</Link>;
  }
  return inner;
}
