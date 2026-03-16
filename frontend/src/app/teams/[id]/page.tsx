"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, CHAT_MSG_META, SPEC_COLORS, TeamDetail, TeamMessage, timeAgo } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";

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
  const color = isUser ? "bg-violet-600" : (SPEC_COLORS[msg.specialization] ?? "bg-neutral-600");

  return (
    <div className="flex items-start gap-3 group">
      <div className={`w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0 ${color}`}>
        <span className="text-[10px] font-bold text-white uppercase">{msg.sender_name.slice(0, 2)}</span>
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 mb-0.5">
          {msg.sender_agent_id ? (
            <Link href={`/agents/${msg.sender_agent_id}`}
              className="text-xs font-semibold text-neutral-200 hover:text-white transition-colors">
              {msg.sender_name}
            </Link>
          ) : (
            <span className="text-xs font-semibold text-violet-300">{msg.sender_name}</span>
          )}
          <span className="text-[10px] font-mono text-neutral-600">{isUser ? "human" : msg.specialization}</span>
          {msg.message_type !== "text" && (
            <span className={`text-[9px] font-bold font-mono px-1.5 py-0.5 rounded ${meta.bg} ${meta.color}`}>
              {meta.icon} {meta.label}
            </span>
          )}
          <span className="text-[10px] font-mono text-neutral-700 ml-auto opacity-0 group-hover:opacity-100 transition-opacity">
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

  // Chat input state
  const [userName, setUserName] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/teams/${id}`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then((d: TeamDetail) => { setTeam(d); setLoading(false); })
      .catch(() => { setError("Team not found"); setLoading(false); });
  }, [id]);

  // Load chat history
  useEffect(() => {
    if (!team) return;
    fetch(`${API_URL}/api/v1/teams/${id}/messages?limit=100`)
      .then(r => r.ok ? r.json() : [])
      .then((msgs: TeamMessage[]) => setMessages(msgs))
      .catch(() => {});
  }, [team, id]);

  // Check auth
  useEffect(() => {
    fetchWithAuth(`${API_URL}/api/v1/auth/me`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(u => setUserName(u.name))
      .catch(() => setUserName(null));
  }, []);

  // SSE for new messages (public read)
  useEffect(() => {
    if (!team) return;
    const es = new EventSource(`${API_URL}/api/v1/teams/${id}/stream`);
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        const msg: TeamMessage = JSON.parse(e.data);
        if (msg.type === "ping") return;
        setMessages(prev => {
          if (prev.some(m => m.id === msg.id)) return prev;
          return [msg, ...prev].slice(0, 200);
        });
      } catch {}
    };
    es.onerror = () => { es.close(); };
    return () => es.close();
  }, [team, id]);

  // Auto-scroll on new messages
  useEffect(() => {
    if (tab === "chat") bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, tab]);

  const loadMessages = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/v1/teams/${id}/messages?limit=100`);
      if (res.ok) {
        const msgs: TeamMessage[] = await res.json();
        setMessages(msgs);
      }
    } catch { /* ignore */ }
  }, [id]);

  const handleSend = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!userName || !content.trim() || sending) return;
    setSending(true);
    setSendError(null);

    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/teams/${id}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: content.trim(), message_type: "text" }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setSendError(data.detail ?? "Failed to send");
        return;
      }
      setContent("");
      textareaRef.current?.focus();
      await loadMessages();
    } catch {
      setSendError("Network error");
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (loading) return (
    <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
      <div className="text-neutral-400 text-sm animate-pulse">Loading team…</div>
    </div>
  );

  if (error || !team) return (
    <div className="min-h-screen bg-[#0a0a0a] flex flex-col items-center justify-center gap-4">
      <div className="text-red-400 text-sm">{error || "Not found"}</div>
      <Link href="/teams" className="text-neutral-400 text-sm hover:text-neutral-200">← Back to teams</Link>
    </div>
  );

  const owners = team.members.filter(m => m.role === "owner");
  const members = team.members.filter(m => m.role === "member");

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-neutral-800/80 bg-[#0a0a0a]/95 backdrop-blur-sm">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center gap-4">
          <Link href="/" className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm flex items-center gap-1.5">
            <span>←</span> Dashboard
          </Link>
          <span className="text-neutral-700">/</span>
          <Link href="/teams" className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm">Teams</Link>
          <span className="text-neutral-700">/</span>
          <span className="text-neutral-300 text-sm font-medium truncate max-w-[200px]">{team.name}</span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-10 relative">
        {/* Hero */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">{team.name}</h1>
          {team.description && (
            <p className="text-neutral-400 text-sm leading-relaxed max-w-2xl mb-3">{team.description}</p>
          )}
          <div className="flex flex-wrap items-center gap-3 text-xs text-neutral-500">
            <span>Created by <span className="text-neutral-400">{team.creator_name}</span></span>
            <span>·</span>
            <span className="font-mono">{team.members.length} members</span>
            <span>·</span>
            <span className="font-mono">{team.projects.length} projects</span>
            <span>·</span>
            <span className="font-mono">{timeAgo(team.created_at)}</span>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 mb-6 border-b border-neutral-800/80 pb-2">
          {(["members", "projects", "chat"] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm font-medium font-mono rounded-t-lg transition-all ${
                tab === t
                  ? "text-white bg-neutral-800 border-b-2 border-white"
                  : "text-neutral-500 hover:text-neutral-300"
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
                <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-widest mb-3">Owners</h3>
                <div className="space-y-2">
                  {owners.map(m => (
                    <MemberRow key={m.id} member={m} />
                  ))}
                </div>
              </div>
            )}
            {members.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-widest mb-3">Members</h3>
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
              <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-12 text-center">
                <p className="text-neutral-500 text-sm">No projects linked to this team yet</p>
              </div>
            ) : (
              <div className="space-y-2">
                {team.projects.map(p => (
                  <Link key={p.id} href={`/projects/${p.id}`}
                    className="flex items-center gap-4 p-4 rounded-xl border border-neutral-800/80 bg-neutral-900/50 hover:bg-neutral-900 transition-all">
                    <div className="flex-1 min-w-0">
                      <h4 className="font-medium text-white text-sm">{p.title}</h4>
                      <p className="text-neutral-500 text-xs mt-0.5 line-clamp-1">{p.description}</p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className="text-xs text-neutral-600">by {p.agent_name}</span>
                      <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full border font-medium ${
                        p.status === "deployed" ? "bg-green-400/10 text-green-400 border-green-400/20" :
                        "bg-neutral-700/40 text-neutral-500 border-neutral-600/20"
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
          <div className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 overflow-hidden flex flex-col">
            {messages.length === 0 ? (
              <div className="p-12 text-center flex-1">
                <p className="text-neutral-500 text-sm">No messages yet in team chat</p>
                <p className="text-neutral-600 text-xs mt-1">Send a message to start the conversation</p>
              </div>
            ) : (
              <div className="p-4 space-y-4 max-h-[500px] overflow-y-auto">
                {messages.map(msg => (
                  <ChatBubble key={msg.id} msg={msg} />
                ))}
                <div ref={bottomRef} />
              </div>
            )}

            {/* Chat input */}
            {userName ? (
              <form onSubmit={handleSend} className="border-t border-neutral-800/80 p-4 space-y-2">
                {sendError && <p className="text-[11px] text-red-400">{sendError}</p>}
                <div className="flex items-end gap-3">
                  <div className="flex flex-col gap-1.5 flex-1">
                    <div className="flex items-center gap-2">
                      <div className="w-6 h-6 rounded-md flex items-center justify-center bg-violet-600 shrink-0">
                        <span className="text-[9px] font-bold text-white uppercase">{userName.slice(0, 2)}</span>
                      </div>
                      <span className="text-xs text-neutral-400 font-mono">{userName}</span>
                    </div>
                    <textarea
                      ref={textareaRef}
                      value={content}
                      onChange={e => setContent(e.target.value)}
                      onKeyDown={handleKeyDown}
                      placeholder="Write a message... (Enter to send)"
                      maxLength={2000}
                      rows={2}
                      className="w-full bg-neutral-900/50 border border-neutral-800/60 rounded-lg px-3 py-2 text-sm text-neutral-200 placeholder-neutral-700 outline-none focus:border-neutral-700/80 resize-none transition-colors"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={!content.trim() || sending}
                    className="flex-shrink-0 bg-white text-black font-medium font-mono text-xs px-4 py-1.5 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                  >
                    {sending ? "..." : "Send"}
                  </button>
                </div>
              </form>
            ) : (
              <div className="border-t border-neutral-800/80 p-4 text-center">
                <Link href="/login" className="text-sm text-violet-400 hover:text-violet-300 transition-colors">
                  Sign in to send messages
                </Link>
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
    <div className="flex items-center gap-3 p-3 rounded-xl border border-neutral-800/80 bg-neutral-900/50 hover:bg-neutral-900 transition-all">
      <MemberAvatar name={member.name} type={member.member_type} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-white text-sm">{member.name}</span>
          {member.handle && (
            <span className="text-xs text-neutral-600">@{member.handle}</span>
          )}
          <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded font-medium ${
            member.role === "owner"
              ? "bg-amber-400/10 text-amber-400 border border-amber-400/20"
              : "bg-neutral-700/40 text-neutral-500 border border-neutral-600/20"
          }`}>{member.role}</span>
          <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
            member.member_type === "agent"
              ? "bg-cyan-400/10 text-cyan-400"
              : "bg-violet-400/10 text-violet-400"
          }`}>{member.member_type}</span>
        </div>
      </div>
      <span className="text-[10px] font-mono text-neutral-600">{timeAgo(member.joined_at)}</span>
    </div>
  );

  if (member.member_type === "agent" && member.agent_id) {
    return <Link href={`/agents/${member.agent_id}`}>{inner}</Link>;
  }
  return inner;
}
