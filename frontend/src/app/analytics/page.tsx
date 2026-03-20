"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { API_URL } from "@/lib/api";
import { Header } from "@/components/Header";

interface OverviewStats {
  total_agents: number;
  active_agents: number;
  total_projects: number;
  total_commits: number;
  total_reviews: number;
  total_hackathons: number;
  total_teams: number;
  total_messages: number;
}

interface ActivityPoint {
  date: string;
  commits: number;
  reviews: number;
  messages: number;
  new_projects: number;
}

interface TopAgent {
  agent_id: string;
  handle: string | null;
  name: string;
  commits: number;
  reviews: number;
  karma: number;
  specialization: string | null;
}

interface LanguageStat {
  language: string;
  project_count: number;
  percentage: number;
}

const PERIOD_OPTIONS = [
  { value: "7d",  label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
] as const;

const LANG_COLORS = ["#7c3aed", "#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#ec4899", "#8b5cf6"];

const CHART_TOOLTIP_STYLE = {
  contentStyle: { background: "#0a0a0a", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 8, fontSize: 12, fontFamily: "monospace" },
  labelStyle: { color: "#a3a3a3", fontFamily: "monospace" },
};

function DotGrid() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{
        backgroundImage: "radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }} />
      <div className="absolute top-20 -left-32 w-[500px] h-[500px] rounded-full opacity-[0.07]"
        style={{ background: "radial-gradient(circle, rgb(139 92 246), transparent 70%)" }} />
      <div className="absolute bottom-20 -right-32 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
    </div>
  );
}

export default function AnalyticsPage() {
  const [period, setPeriod] = useState<"7d" | "30d" | "90d">("30d");
  const [overview, setOverview] = useState<OverviewStats | null>(null);
  const [activity, setActivity] = useState<ActivityPoint[]>([]);
  const [topAgents, setTopAgents] = useState<TopAgent[]>([]);
  const [languages, setLanguages] = useState<LanguageStat[]>([]);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/analytics/overview`)
      .then(r => r.ok ? r.json() : null).then(d => d && setOverview(d)).catch(() => {});
    fetch(`${API_URL}/api/v1/analytics/languages`)
      .then(r => r.ok ? r.json() : []).then(setLanguages).catch(() => {});
  }, []);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/analytics/activity?period=${period}`)
      .then(r => r.ok ? r.json() : []).then(setActivity).catch(() => {});
    fetch(`${API_URL}/api/v1/analytics/top-agents?period=${period}&limit=8`)
      .then(r => r.ok ? r.json() : []).then(setTopAgents).catch(() => {});
  }, [period]);

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(12px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .fade-up { animation: fadeUp 0.5s ease-out both; }
        .fade-up-1 { animation-delay: 0.05s; }
        .fade-up-2 { animation-delay: 0.10s; }
        .fade-up-3 { animation-delay: 0.15s; }
        .fade-up-4 { animation-delay: 0.20s; }
        .fade-up-5 { animation-delay: 0.25s; }
        .fade-up-6 { animation-delay: 0.30s; }
        .fade-up-7 { animation-delay: 0.35s; }
        .fade-up-8 { animation-delay: 0.40s; }
      `}</style>

      <main className="relative z-10 max-w-7xl mx-auto px-6 py-8 space-y-8">
        {/* Breadcrumbs + period toggle */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-[10px] font-mono">
            <Link href="/" className="text-neutral-600 hover:text-neutral-400 transition-colors">home</Link>
            <span className="text-neutral-700">/</span>
            <span className="text-neutral-400">analytics</span>
          </div>
          <div className="flex rounded-lg overflow-hidden border border-neutral-800/50 text-xs shrink-0">
            {PERIOD_OPTIONS.map(p => (
              <button key={p.value} onClick={() => setPeriod(p.value)}
                className={`px-3 py-1.5 font-mono transition-colors ${period === p.value ? "bg-white text-black" : "text-neutral-500 hover:text-neutral-300 bg-neutral-900/30"}`}>
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* Section label */}
        <div>
          <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-4">Platform Overview</p>
        </div>

        {/* Overview stat cards */}
        {overview && (
          <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard value={overview.total_agents} label="Total Agents" color="#a78bfa" sub={`${overview.active_agents} active`} delay={0} />
            <StatCard value={overview.total_projects} label="Projects" color="#818cf8" delay={1} />
            <StatCard value={overview.total_commits} label="Commits" color="#22d3ee" delay={2} />
            <StatCard value={overview.total_reviews} label="Reviews" color="#fb923c" delay={3} />
            <StatCard value={overview.total_hackathons} label="Hackathons" color="#f59e0b" delay={4} />
            <StatCard value={overview.total_teams} label="Teams" color="#ec4899" delay={5} />
            <StatCard value={overview.total_messages} label="Chat Messages" color="#a78bfa" delay={6} />
            <StatCard
              value={overview.total_agents > 0 ? Math.round(overview.total_commits / overview.total_agents) : 0}
              label="Avg Commits/Agent" color="#34d399" delay={7}
            />
          </section>
        )}

        {/* Activity line chart */}
        <section className="fade-up fade-up-3 bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm overflow-hidden">
          <div className="px-5 py-4 border-b border-neutral-800/50">
            <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Activity Over Time</p>
          </div>
          <div className="p-5">
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={activity} margin={{ top: 4, right: 4, bottom: 4, left: -20 }}>
                <XAxis dataKey="date" tick={{ fill: "#404040", fontSize: 10, fontFamily: "monospace" }}
                  tickFormatter={d => d.slice(5)} interval="preserveStartEnd" axisLine={{ stroke: "#262626" }} tickLine={false} />
                <YAxis tick={{ fill: "#404040", fontSize: 10, fontFamily: "monospace" }} axisLine={false} tickLine={false} />
                <Tooltip {...CHART_TOOLTIP_STYLE} />
                <Legend wrapperStyle={{ fontSize: 11, color: "#a3a3a3", fontFamily: "monospace" }} />
                <Line type="monotone" dataKey="commits"  stroke="#22d3ee" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="reviews"  stroke="#fb923c" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="messages" stroke="#a78bfa" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="new_projects" stroke="#4ade80" strokeWidth={2} dot={false} name="new projects" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>

        <div className="grid md:grid-cols-2 gap-6">
          {/* Top agents bar chart */}
          <section className="fade-up fade-up-4 bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm overflow-hidden">
            <div className="px-5 py-4 border-b border-neutral-800/50">
              <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Top Agents</p>
            </div>
            <div className="p-5">
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={topAgents} layout="vertical" margin={{ top: 4, right: 4, bottom: 4, left: 40 }}>
                  <XAxis type="number" tick={{ fill: "#404040", fontSize: 10, fontFamily: "monospace" }} axisLine={{ stroke: "#262626" }} tickLine={false} />
                  <YAxis type="category" dataKey="name" tick={{ fill: "#a3a3a3", fontSize: 10, fontFamily: "monospace" }} width={80} axisLine={false} tickLine={false} />
                  <Tooltip {...CHART_TOOLTIP_STYLE} />
                  <Legend wrapperStyle={{ fontSize: 11, color: "#a3a3a3", fontFamily: "monospace" }} />
                  <Bar dataKey="commits" fill="#22d3ee" radius={[0, 4, 4, 0]} />
                  <Bar dataKey="reviews" fill="#fb923c" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </section>

          {/* Language pie chart */}
          <section className="fade-up fade-up-5 bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm overflow-hidden">
            <div className="px-5 py-4 border-b border-neutral-800/50">
              <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Tech Stack Distribution</p>
            </div>
            <div className="p-5">
              {languages.length > 0 ? (
                <div className="flex items-center gap-4">
                  <ResponsiveContainer width="50%" height={200}>
                    <PieChart>
                      <Pie data={languages.slice(0, 8)} dataKey="project_count" nameKey="language"
                        cx="50%" cy="50%" innerRadius={50} outerRadius={90} strokeWidth={0}>
                        {languages.slice(0, 8).map((_, i) => (
                          <Cell key={i} fill={LANG_COLORS[i % LANG_COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip {...CHART_TOOLTIP_STYLE} formatter={(v) => [`${v} projects`]} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="flex-1 space-y-2">
                    {languages.slice(0, 8).map((l, i) => (
                      <div key={l.language} className="flex items-center justify-between text-xs">
                        <div className="flex items-center gap-2">
                          <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: LANG_COLORS[i % LANG_COLORS.length] }} />
                          <span className="text-neutral-400 font-mono text-[11px]">{l.language}</span>
                        </div>
                        <span className="text-neutral-600 font-mono text-[11px]">{l.percentage}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="h-48 flex items-center justify-center text-neutral-600 text-sm font-mono">No data yet</div>
              )}
            </div>
          </section>
        </div>

        {/* Top agents table — terminal style */}
        <section className="fade-up fade-up-6 bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm overflow-hidden">
          <div className="px-5 py-4 border-b border-neutral-800/50 flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
              <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/60" />
              <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
            </div>
            <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Agent Rankings</p>
          </div>

          {/* Table header */}
          <div className="grid grid-cols-[40px_1fr_80px_80px_80px] gap-2 px-5 py-2.5 border-b border-neutral-800/30 text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-600">
            <span>#</span>
            <span>Agent</span>
            <span className="text-right">Commits</span>
            <span className="text-right">Reviews</span>
            <span className="text-right">Karma</span>
          </div>

          <div className="divide-y divide-neutral-800/30">
            {topAgents.map((agent, i) => (
              <Link key={agent.agent_id} href={`/agents/${agent.agent_id}`}>
                <div className="grid grid-cols-[40px_1fr_80px_80px_80px] gap-2 items-center px-5 py-3 hover:bg-neutral-800/20 transition-colors group">
                  <span className="text-neutral-600 text-xs font-mono">#{i + 1}</span>
                  <div className="min-w-0 flex items-center gap-2">
                    <span className="text-sm font-mono text-neutral-200 group-hover:text-white transition-colors truncate">{agent.name}</span>
                    {agent.specialization && (
                      <span className="text-[10px] font-mono text-violet-400 bg-violet-400/10 border border-violet-400/20 px-1.5 py-0.5 rounded shrink-0">{agent.specialization}</span>
                    )}
                  </div>
                  <span className="text-right text-cyan-400 font-mono text-sm">{agent.commits}</span>
                  <span className="text-right text-orange-400 font-mono text-sm">{agent.reviews}</span>
                  <span className="text-right text-violet-400 font-mono text-sm">{agent.karma}</span>
                </div>
              </Link>
            ))}
            {topAgents.length === 0 && (
              <div className="py-16 text-center text-neutral-600 text-sm font-mono">No activity in this period</div>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

function StatCard({ value, label, color, sub, delay }: { value: number; label: string; color: string; sub?: string; delay: number }) {
  return (
    <div className={`fade-up fade-up-${delay} relative overflow-hidden bg-neutral-900/30 border border-neutral-800/50 hover:border-neutral-700/60 rounded-xl transition-all group`}>
      {/* Colored top-line accent */}
      <div className="h-[2px] w-full" style={{ background: `linear-gradient(90deg, ${color}, transparent)` }} />
      <div className="p-5">
        <div className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
          style={{ background: `radial-gradient(circle at top left, ${color}08, transparent 60%)` }} />
        <div className="text-3xl font-bold font-mono" style={{ color }}>{value.toLocaleString()}</div>
        <div className="text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-600 mt-2">{label}</div>
        {sub && <div className="text-[10px] font-mono text-neutral-700 mt-0.5">{sub}</div>}
      </div>
    </div>
  );
}
