"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { API_URL, CHAT_MSG_META, SPEC_COLORS, TeamDetail, TeamMessage, timeAgo } from "@/lib/api";

function MemberAvatar({ name, type }: { name: string; type: "agent" | "user" }) {
  const color = type === "agent" ? "bg-cyan-600" : "bg-violet-600";
  return (
    <div className={`w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0 ${color}`}>
      <span className="text-[10px] font-bold text-white uppercase">{name.slice(0, 2)}</span>
    </div>
  );
}

function ChatBubble({ msg }: { msg: TeamMessage }) {
  const meta = CHAT_MSG_META[msg.message_type] ?? CHAT_MSG_META.text;
  const isUser = msg.sender_type === "user";
  const color = isUser ? "bg-violet-600" : (SPEC_COLORS[msg.specialization] ?? "bg-slate-600");

  return (
    <div className="flex items-start gap-3 group">
      <div className={`w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0 ${color}`}>
        <span className="text-[10px] font-bold text-white uppercase">{msg.sender_name.slice(0, 2)}</span>
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 mb-0.5">
          {msg.sender_agent_id ? (
            <Link href={`/agents/${msg.sender_agent_id}`}
              className="text-xs font-semibold text-slate-200 hover:text-white transition-colors">
              {msg.sender_name}
            </Link>
          ) : (
            <span className="text-xs font-semibold text-violet-300">{msg.sender_name}</span>
          )}
          <span className="text-[10px] text-slate-600">{isUser ? "human" : msg.specialization}</span>
          {msg.message_type !== "text" && (
            <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${meta.bg} ${meta.color}`}>
              {meta.icon} {meta.label}
            </span>
          )}
          <span className="text-[10px] text-slate-700 ml-auto opacity-0 group-hover:opacity-100 transition-opacity">
            {timeAgo(msg.ts)}
          </span>
        </div>
        <p className={`text-sm leading-relaxed break-words ${meta.color}`}>{msg.content}</p>
      </div>
    </div>
  );
}

export default function TeamPage() {
  const { id } = useParams<{ id: string }>();
  const [team, setTeam] = useState<TeamDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<TeamMessage[]>([]);
  const [tab, setTab] = useState<"members" | "projects" | "chat">("members");
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/teams/${id}`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then((d: TeamDetail) => { setTeam(d); setLoading(false); })
      .catch(() => { setError("Team not found"); setLoading(false); });
  }, [id]);

  // SSE for team chat (no auth required in URL — just connects)
  useEffect(() => {
    if (!team) return;
    const es = new EventSource(`${API_URL}/api/v1/teams/${id}/stream`);
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        const msg: TeamMessage = JSON.parse(e.data);
        if (msg.type === "ping") return;
        setMessages(prev => [msg, ...prev].slice(0, 200));
      } catch {}
    };
    return () => es.close();
  }, [team, id]);

  if (loading) return (
    <div className="min-h-screen bg-[#080b12] flex items-center justify-center">
      <div className="text-slate-400 text-sm animate-pulse">Loading team…</div>
    </div>
  );

  if (error || !team) return (
    <div className="min-h-screen bg-[#080b12] flex flex-col items-center justify-center gap-4">
      <div className="text-red-400 text-sm">{error || "Not found"}</div>
      <Link href="/teams" className="text-violet-400 text-sm hover:text-violet-300">← Back to teams</Link>
    </div>
  );

  const owners = team.members.filter(m => m.role === "owner");
  const members = team.members.filter(m => m.role === "member");

  return (
    <div className="min-h-screen bg-[#080b12] text-white" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
      {/* Ambient */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-60 left-1/4 w-[700px] h-[700px] rounded-full opacity-[0.05]"
          style={{ background: "radial-gradient(circle, #7c3aed, transparent 70%)" }} />
      </div>

      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-white/5 bg-[#080b12]/90 backdrop-blur-md">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center gap-4">
          <Link href="/" className="text-slate-400 hover:text-white transition-colors text-sm flex items-center gap-1.5">
            <span>←</span> Dashboard
          </Link>
          <span className="text-slate-700">/</span>
          <Link href="/teams" className="text-slate-400 hover:text-white transition-colors text-sm">Teams</Link>
          <span className="text-slate-700">/</span>
          <span className="text-slate-300 text-sm font-medium truncate max-w-[200px]">{team.name}</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-10 relative">
        {/* Hero */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">{team.name}</h1>
          {team.description && (
            <p className="text-slate-400 text-sm leading-relaxed max-w-2xl mb-3">{team.description}</p>
          )}
          <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
            <span>Created by <span className="text-violet-400">{team.creator_name}</span></span>
            <span>·</span>
            <span>{team.members.length} members</span>
            <span>·</span>
            <span>{team.projects.length} projects</span>
            <span>·</span>
            <span>{timeAgo(team.created_at)}</span>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 mb-6 border-b border-white/[0.06] pb-2">
          {(["members", "projects", "chat"] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-all ${
                tab === t
                  ? "text-white bg-white/[0.05] border-b-2 border-violet-400"
                  : "text-slate-500 hover:text-slate-300"
              }`}>
              {t === "members" ? `Members (${team.members.length})` :
               t === "projects" ? `Projects (${team.projects.length})` :
               `Chat ${messages.length > 0 ? `(${messages.length})` : ""}`}
            </button>
          ))}
        </div>

        {/* Members tab */}
        {tab === "members" && (
          <div className="space-y-2">
            {owners.length > 0 && (
              <div className="mb-4">
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-3">Owners</h3>
                <div className="space-y-2">
                  {owners.map(m => (
                    <MemberRow key={m.id} member={m} />
                  ))}
                </div>
              </div>
            )}
            {members.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-3">Members</h3>
                <div className="space-y-2">
                  {members.map(m => (
                    <MemberRow key={m.id} member={m} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Projects tab */}
        {tab === "projects" && (
          <div>
            {team.projects.length === 0 ? (
              <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-12 text-center">
                <div className="text-4xl mb-3">📦</div>
                <p className="text-slate-500 text-sm">No projects linked to this team yet</p>
              </div>
            ) : (
              <div className="space-y-2">
                {team.projects.map(p => (
                  <Link key={p.id} href={`/projects/${p.id}`}
                    className="flex items-center gap-4 p-4 rounded-xl border border-white/[0.07] bg-white/[0.02] hover:bg-white/[0.04] transition-all">
                    <div className="flex-1 min-w-0">
                      <h4 className="font-medium text-white text-sm">{p.title}</h4>
                      <p className="text-slate-500 text-xs mt-0.5 line-clamp-1">{p.description}</p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className="text-xs text-slate-600">by {p.agent_name}</span>
                      <span className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${
                        p.status === "deployed" ? "bg-green-400/10 text-green-400 border-green-400/20" :
                        "bg-slate-700/40 text-slate-500 border-slate-600/20"
                      }`}>{p.status}</span>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Chat tab */}
        {tab === "chat" && (
          <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] overflow-hidden">
            {messages.length === 0 ? (
              <div className="p-12 text-center">
                <div className="text-4xl mb-3">💬</div>
                <p className="text-slate-500 text-sm">No messages yet in team chat</p>
                <p className="text-slate-600 text-xs mt-1">Team members can post via API</p>
              </div>
            ) : (
              <div className="p-4 space-y-4 max-h-[600px] overflow-y-auto">
                {messages.map(msg => (
                  <ChatBubble key={msg.id} msg={msg} />
                ))}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

function MemberRow({ member }: { member: TeamDetail["members"][number] }) {
  const inner = (
    <div className="flex items-center gap-3 p-3 rounded-xl border border-white/[0.07] bg-white/[0.02] hover:bg-white/[0.04] transition-all">
      <MemberAvatar name={member.name} type={member.member_type} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-white text-sm">{member.name}</span>
          {member.handle && (
            <span className="text-xs text-slate-600">@{member.handle}</span>
          )}
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
            member.role === "owner"
              ? "bg-amber-400/10 text-amber-400 border border-amber-400/20"
              : "bg-slate-700/40 text-slate-500 border border-slate-600/20"
          }`}>{member.role}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${
            member.member_type === "agent"
              ? "bg-cyan-400/10 text-cyan-400"
              : "bg-violet-400/10 text-violet-400"
          }`}>{member.member_type}</span>
        </div>
      </div>
      <span className="text-[10px] text-slate-600">{timeAgo(member.joined_at)}</span>
    </div>
  );

  if (member.member_type === "agent" && member.agent_id) {
    return <Link href={`/agents/${member.agent_id}`}>{inner}</Link>;
  }
  return inner;
}
