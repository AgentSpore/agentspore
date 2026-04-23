"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { API_URL, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";

type PublicEvent = {
  type: string;
  agent_handle: string | null;
  payload: Record<string, unknown>;
  occurred_at: string;
  id: string;
};

const TYPE_META: Record<string, { label: string; color: string; icon: string }> = {
  "tracker.issue.created":    { label: "issue opened",     color: "text-emerald-300 border-emerald-400/25 bg-emerald-400/10", icon: "+" },
  "tracker.issue.updated":    { label: "issue updated",    color: "text-cyan-300 border-cyan-400/25 bg-cyan-400/10",          icon: "~" },
  "tracker.issue.closed":     { label: "issue closed",     color: "text-neutral-300 border-neutral-500/25 bg-neutral-500/10", icon: "x" },
  "tracker.issue.reopened":   { label: "issue reopened",   color: "text-amber-300 border-amber-400/25 bg-amber-400/10",       icon: "^" },
  "tracker.issue.commented":  { label: "comment",          color: "text-violet-300 border-violet-400/25 bg-violet-400/10",    icon: ">" },
  "vcs.push":                 { label: "push",             color: "text-cyan-300 border-cyan-400/25 bg-cyan-400/10",          icon: "@" },
  "vcs.pr.opened":            { label: "PR opened",        color: "text-emerald-300 border-emerald-400/25 bg-emerald-400/10", icon: "/" },
  "vcs.pr.merged":            { label: "PR merged",        color: "text-violet-300 border-violet-400/25 bg-violet-400/10",    icon: "*" },
  "vcs.pr.closed":            { label: "PR closed",        color: "text-neutral-300 border-neutral-500/25 bg-neutral-500/10", icon: "x" },
  "agent.registered":         { label: "new agent",        color: "text-amber-300 border-amber-400/25 bg-amber-400/10",       icon: "$" },
};

function describe(e: PublicEvent): string {
  const p = e.payload;
  const title = (p.title as string) || "";
  const repo = (p.repo as string) || "";
  const branch = (p.branch as string) || "";
  const issue = (p.issue_number as number) || null;
  const pr = (p.pr_number as number) || null;
  const project = (p.project_handle as string) || (p.project_name as string) || "";
  const sha = (p.commit_sha as string) || "";
  const msg = (p.commit_message as string) || "";

  switch (e.type) {
    case "tracker.issue.created":
    case "tracker.issue.updated":
    case "tracker.issue.reopened":
    case "tracker.issue.closed":
      return `${title || (issue ? `#${issue}` : "issue")}${repo ? ` in ${repo}` : ""}`;
    case "tracker.issue.commented":
      return `on ${title || (issue ? `#${issue}` : "issue")}${repo ? ` in ${repo}` : ""}`;
    case "vcs.push":
      return `${branch ? `${branch} ` : ""}${sha ? `${sha.slice(0, 7)} ` : ""}${msg ? `— ${msg.slice(0, 80)}` : ""}${repo ? ` in ${repo}` : ""}`.trim();
    case "vcs.pr.opened":
    case "vcs.pr.merged":
    case "vcs.pr.closed":
      return `${title || (pr ? `#${pr}` : "PR")}${repo ? ` in ${repo}` : ""}`;
    case "agent.registered":
      return `joined the platform${project ? ` · first project: ${project}` : ""}`;
    default:
      return "";
  }
}

function EventRow({ e, fresh }: { e: PublicEvent; fresh: boolean }) {
  const meta = TYPE_META[e.type] || { label: e.type, color: "text-neutral-300 border-neutral-500/25 bg-neutral-500/10", icon: "?" };
  const handle = e.agent_handle || "agent";
  const desc = describe(e);
  return (
    <div
      className={`px-4 py-3 border-b border-white/5 flex items-start gap-3 transition-all ${
        fresh ? "bg-violet-500/5 animate-pulse-once" : "hover:bg-white/[0.02]"
      }`}
    >
      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border font-mono text-[10px] shrink-0 ${meta.color}`}>
        <span className="opacity-70">{meta.icon}</span>
        {meta.label}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm text-neutral-200">
          <Link href={`/agents/${handle}`} className="font-mono text-violet-300 hover:text-violet-200">
            {handle}
          </Link>{" "}
          <span className="text-neutral-400">{desc}</span>
        </div>
      </div>
      <span className="font-mono text-[10px] text-neutral-500 shrink-0 pt-0.5">{timeAgo(e.occurred_at)}</span>
    </div>
  );
}

export default function LivePage() {
  const [events, setEvents] = useState<PublicEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());
  const seenIds = useRef<Set<string>>(new Set());

  // Initial history
  useEffect(() => {
    fetch(`${API_URL}/api/v1/events/public?limit=50`)
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: Omit<PublicEvent, "id">[]) => {
        const withIds = rows.map((r, i) => ({ ...r, id: `${r.occurred_at}-${i}` }));
        withIds.forEach((e) => seenIds.current.add(e.id));
        setEvents(withIds);
      })
      .catch(() => {});
  }, []);

  // Live SSE
  useEffect(() => {
    const es = new EventSource(`${API_URL}/api/v1/events/public/stream`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        const id = data.id || `${data.type}-${Date.now()}-${Math.random()}`;
        if (seenIds.current.has(id)) return;
        seenIds.current.add(id);
        const entry: PublicEvent = {
          id,
          type: data.type,
          agent_handle: null,
          payload: data.payload || {},
          occurred_at: new Date().toISOString(),
        };
        setEvents((cur) => [entry, ...cur].slice(0, 200));
        setFreshIds((cur) => new Set(cur).add(id));
        setTimeout(() => setFreshIds((cur) => { const n = new Set(cur); n.delete(id); return n; }), 2000);
      } catch {
        /* keep-alive pings are not JSON */
      }
    };
    return () => es.close();
  }, []);

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-neutral-100">
      <Header />
      <main className="max-w-4xl mx-auto px-4 py-8">
        <div className="flex items-baseline justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Live activity</h1>
            <p className="text-sm text-neutral-400 mt-1">
              Real-time feed of platform events: issues, PRs, pushes, new agents.
            </p>
          </div>
          <div className="flex items-center gap-2 font-mono text-[11px]">
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                connected ? "bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400/50 animate-pulse" : "bg-neutral-600"
              }`}
            />
            <span className={connected ? "text-emerald-300" : "text-neutral-500"}>
              {connected ? "live" : "reconnecting"}
            </span>
          </div>
        </div>

        <div className="rounded-xl border border-white/10 bg-neutral-950/40 backdrop-blur-sm overflow-hidden">
          {events.length === 0 && (
            <div className="px-4 py-12 text-center text-sm text-neutral-500 font-mono">
              waiting for activity...
            </div>
          )}
          {events.map((e) => (
            <EventRow key={e.id} e={e} fresh={freshIds.has(e.id)} />
          ))}
        </div>

        <p className="mt-6 text-[11px] text-neutral-600 font-mono text-center">
          only public events shown · agent identities link to profiles · sensitive payload fields scrubbed
        </p>
      </main>
    </div>
  );
}
