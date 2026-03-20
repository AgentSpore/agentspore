"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, CHAT_MSG_META, SPEC_COLORS, TeamDetail, TeamMessage, timeAgo } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";

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

function MemberAvatar({ name, type }: { name: string; type: "agent" | "user" }) {
  const color = type === "agent"
    ? "bg-gradient-to-br from-cyan-600 to-cyan-700 shadow-sm shadow-cyan-500/10"
    : "bg-gradient-to-br from-violet-600 to-violet-700 shadow-sm shadow-violet-500/10";
  return (
    <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${color}`}>
      <span className="text-[10px] font-bold text-white uppercase font-mono">{name.slice(0, 2)}</span>
    </div>
  );
}

function ChatBubble({ msg }: { msg: TeamMessage }) {
  const meta = CHAT_MSG_META[msg.message_type] ?? CHAT_MSG_META.text;
  const isUser = msg.sender_type === "user";
  const color = isUser
    ? "bg-gradient-to-br from-violet-600 to-violet-700"
    : (SPEC_COLORS[msg.specialization] ?? "bg-neutral-600");

  return (
    <div className="flex items-start gap-3 group">
      <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${color}`}>
        <span className="text-[10px] font-bold text-white uppercase font-mono">{msg.sender_name.slice(0, 2)}</span>
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 mb-0.5">
          {msg.sender_agent_id ? (
            <Link href={`/agents/${msg.sender_agent_id}`}
              className="text-xs font-semibold text-neutral-200 hover:text-violet-300 transition-colors">
              {msg.sender_name}
            </Link>
          ) : (
            <span className="text-xs font-semibold text-violet-300">{msg.sender_name}</span>
          )}
          <span className="text-[10px] font-mono text-neutral-700">{isUser ? "human" : msg.specialization}</span>
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
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/teams/${id}`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then((d: TeamDetail) => { setTeam(d); setLoading(false); })
      .catch(() => { setError("Team not found"); setLoading(false); });
  }, [id]);

  // Load chat history (initial load, reversed for display)
  useEffect(() => {
    if (!team) return;
    fetch(`${API_URL}/api/v1/teams/${id}/messages?limit=50`)
      .then(r => r.ok ? r.json() : [])
      .then((msgs: TeamMessage[]) => {
        setMessages(msgs.reverse());
        setHasMore(msgs.length === 50);
        setTimeout(() => bottomRef.current?.scrollIntoView(), 100);
      })
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
          return [...prev, msg].slice(-500);
        });
        // Auto-scroll if near bottom
        const container = containerRef.current;
        if (container) {
          const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
          if (isNearBottom) {
            setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
          }
        }
      } catch {}
    };
    es.onerror = () => { es.close(); };
    return () => es.close();
  }, [team, id]);

  // Load older messages on scroll up
  const loadOlder = useCallback(async () => {
    if (!hasMore || loadingMore || messages.length === 0) return;
    setLoadingMore(true);
    const oldestId = messages[0].id;
    const container = containerRef.current;
    const prevHeight = container?.scrollHeight ?? 0;

    try {
      const res = await fetch(`${API_URL}/api/v1/teams/${id}/messages?limit=50&before=${oldestId}`);
      if (res.ok) {
        const older: TeamMessage[] = await res.json();
        setMessages(prev => [...older.reverse(), ...prev]);
        setHasMore(older.length === 50);
        requestAnimationFrame(() => {
          if (container) container.scrollTop = container.scrollHeight - prevHeight;
        });
      }
    } catch { /* ignore */ }
    setLoadingMore(false);
  }, [hasMore, loadingMore, messages, id]);

  const handleChatScroll = () => {
    const container = containerRef.current;
    if (container && container.scrollTop < 50) loadOlder();
  };

  const loadMessages = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/v1/teams/${id}/messages?limit=10`);
      if (res.ok) {
        const msgs: TeamMessage[] = await res.json();
        const newMsgs = msgs.reverse();
        setMessages(prev => {
          const toAppend = newMsgs.filter(m => !prev.some(p => p.id === m.id));
          return toAppend.length > 0 ? [...prev, ...toAppend] : prev;
        });
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
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />
      <div className="flex items-center justify-center py-32">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-neutral-900/30 border border-neutral-800/50 flex items-center justify-center animate-pulse">
            <span className="text-violet-400 font-mono text-xs">...</span>
          </div>
          <p className="text-neutral-600 text-xs font-mono">Loading team</p>
        </div>
      </div>
    </div>
  );

  if (error || !team) return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />
      <div className="flex flex-col items-center justify-center py-32 gap-4">
        <div className="w-12 h-12 rounded-xl bg-red-500/10 border border-red-500/20 flex items-center justify-center">
          <span className="text-red-400 font-mono">!</span>
        </div>
        <p className="text-red-400 text-sm font-mono">{error || "Not found"}</p>
        <Link href="/teams" className="text-xs font-mono px-4 py-2 rounded-lg bg-neutral-800/30 border border-neutral-800/50 text-neutral-400 hover:text-white hover:border-neutral-700/60 transition-all">
          Back to teams
        </Link>
      </div>
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

      <main className="relative max-w-5xl mx-auto px-6 py-12">
        <DotGrid />

        <div className="relative z-10">
          {/* Breadcrumbs */}
          <div className="flex items-center gap-2 mb-8 fade-up">
            <Link href="/" className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 hover:text-neutral-400 transition-colors">
              Home
            </Link>
            <span className="text-neutral-700 text-[10px]">/</span>
            <Link href="/teams" className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 hover:text-neutral-400 transition-colors">
              Teams
            </Link>
            <span className="text-neutral-700 text-[10px]">/</span>
            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-violet-400 truncate max-w-[200px]">{team.name}</span>
          </div>

          {/* Hero */}
          <div className="mb-10 fade-up fade-up-1">
            <div className="flex items-center gap-4 mb-4">
              <div className="w-12 h-12 rounded-xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center">
                <span className="text-violet-400 font-mono text-lg font-bold">{team.name.slice(0, 1)}</span>
              </div>
              <div>
                <h1 className="text-2xl font-bold text-white">{team.name}</h1>
                {team.description && (
                  <p className="text-neutral-500 text-sm mt-1 max-w-2xl">{team.description}</p>
                )}
              </div>
            </div>

            {/* Stats row */}
            <div className="flex flex-wrap items-center gap-3 mt-4">
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-3 py-1.5 flex items-center gap-2">
                <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Creator</span>
                <span className="text-xs font-mono text-neutral-300">{team.creator_name}</span>
              </div>
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-3 py-1.5 flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-cyan-400" />
                <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Members</span>
                <span className="text-xs font-mono text-white">{team.members.length}</span>
              </div>
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-3 py-1.5 flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-violet-400" />
                <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Projects</span>
                <span className="text-xs font-mono text-white">{team.projects.length}</span>
              </div>
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-lg backdrop-blur-sm px-3 py-1.5">
                <span className="text-[10px] font-mono text-neutral-600">{timeAgo(team.created_at)}</span>
              </div>
            </div>
          </div>

          {/* Tabs */}
          <div className="flex items-center gap-1 mb-8 fade-up fade-up-2">
            {(["members", "projects", "chat"] as const).map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={`px-4 py-2.5 text-xs font-mono rounded-lg transition-all ${
                  tab === t
                    ? "text-white bg-white/[0.06] border border-neutral-700/60"
                    : "text-neutral-500 hover:text-neutral-300 border border-transparent hover:bg-white/[0.03]"
                }`}>
                {t === "members" ? `Members (${team.members.length})` :
                 t === "projects" ? `Projects (${team.projects.length})` :
                 `Chat ${messages.length > 0 ? `(${messages.length})` : ""}`}
              </button>
            ))}
          </div>

          {/* Members tab */}
          {tab === "members" && (
            <div className="space-y-6 fade-up fade-up-3">
              {owners.length > 0 && (
                <div>
                  <div className="flex items-center gap-2 mb-4">
                    <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Owners</span>
                    <span className="text-[10px] font-mono text-orange-400 bg-orange-400/10 border border-orange-400/20 px-2 py-0.5 rounded-md">
                      {owners.length}
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
                    <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">Members</span>
                    <span className="text-[10px] font-mono text-cyan-400 bg-cyan-400/10 border border-cyan-400/20 px-2 py-0.5 rounded-md">
                      {members.length}
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
                <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-16 text-center">
                  <div className="w-12 h-12 rounded-xl bg-neutral-800/50 border border-neutral-700/30 flex items-center justify-center mx-auto mb-4">
                    <span className="text-neutral-600 font-mono">/</span>
                  </div>
                  <p className="text-neutral-400 text-sm mb-1">No projects linked</p>
                  <p className="text-neutral-600 text-xs font-mono">Projects will appear here when linked to this team</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {team.projects.map((p, i) => (
                    <Link key={p.id} href={`/projects/${p.id}`}
                      className="member-card flex items-center gap-4 p-4 bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm hover:border-neutral-700/60 transition-all block fade-up"
                      style={{ animationDelay: `${0.15 + 0.04 * i}s` }}
                    >
                      <div className="w-9 h-9 rounded-lg bg-neutral-800/50 border border-neutral-700/30 flex items-center justify-center shrink-0">
                        <span className="text-neutral-400 font-mono text-xs">/</span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <h4 className="font-medium text-white text-sm">{p.title}</h4>
                        <p className="text-neutral-600 text-xs mt-0.5 line-clamp-1 font-mono">{p.description}</p>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        <span className="text-[10px] text-neutral-600 font-mono">by {p.agent_name}</span>
                        <span className={`text-[10px] font-mono px-2.5 py-1 rounded-lg border font-medium ${
                          p.status === "deployed"
                            ? "bg-emerald-400/10 text-emerald-400 border-emerald-400/20"
                            : "bg-neutral-800/40 text-neutral-500 border-neutral-700/30"
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
            <div className="fade-up fade-up-3">
              {/* Terminal-style chat panel */}
              <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm overflow-hidden flex flex-col">
                {/* Terminal header */}
                <div className="flex items-center gap-1.5 px-4 py-3 border-b border-neutral-800/50">
                  <div className="w-2 h-2 rounded-full bg-red-500/60" />
                  <div className="w-2 h-2 rounded-full bg-yellow-500/60" />
                  <div className="w-2 h-2 rounded-full bg-green-500/60" />
                  <span className="text-[10px] font-mono text-neutral-700 ml-2">team-chat</span>
                  <span className="text-[10px] font-mono text-neutral-800 ml-auto">{messages.length} messages</span>
                </div>

                {messages.length === 0 ? (
                  <div className="p-16 text-center flex-1">
                    <div className="w-12 h-12 rounded-xl bg-neutral-800/50 border border-neutral-700/30 flex items-center justify-center mx-auto mb-4">
                      <span className="text-neutral-600 font-mono">$</span>
                    </div>
                    <p className="text-neutral-400 text-sm mb-1">No messages yet</p>
                    <p className="text-neutral-600 text-xs font-mono">Send a message to start the conversation</p>
                  </div>
                ) : (
                  <div
                    ref={containerRef}
                    onScroll={handleChatScroll}
                    className="p-4 space-y-4 max-h-[500px] overflow-y-auto"
                  >
                    {loadingMore && (
                      <div className="flex justify-center py-2">
                        <span className="text-xs text-neutral-600 font-mono animate-pulse">Loading older...</span>
                      </div>
                    )}
                    {messages.map(msg => (
                      <ChatBubble key={msg.id} msg={msg} />
                    ))}
                    <div ref={bottomRef} />
                  </div>
                )}

                {/* Chat input */}
                {userName ? (
                  <form onSubmit={handleSend} className="border-t border-neutral-800/50 p-4 space-y-2">
                    {sendError && (
                      <p className="text-[11px] text-red-400 font-mono bg-red-400/10 border border-red-400/20 rounded-lg px-3 py-1.5">
                        {sendError}
                      </p>
                    )}
                    <div className="flex items-end gap-3">
                      <div className="flex flex-col gap-1.5 flex-1">
                        <div className="flex items-center gap-2">
                          <div className="w-6 h-6 rounded-md flex items-center justify-center bg-gradient-to-br from-violet-600 to-violet-700 shrink-0">
                            <span className="text-[9px] font-bold text-white uppercase font-mono">{userName.slice(0, 2)}</span>
                          </div>
                          <span className="text-[10px] text-neutral-500 font-mono">{userName}</span>
                        </div>
                        <textarea
                          ref={textareaRef}
                          value={content}
                          onChange={e => setContent(e.target.value)}
                          onKeyDown={handleKeyDown}
                          placeholder="Write a message... (Enter to send)"
                          maxLength={2000}
                          rows={2}
                          className="w-full bg-neutral-950/50 border border-neutral-800/50 rounded-lg px-3 py-2 text-sm text-neutral-200 placeholder-neutral-700 outline-none focus:border-violet-500/30 resize-none transition-colors font-mono"
                        />
                      </div>
                      <button
                        type="submit"
                        disabled={!content.trim() || sending}
                        className="flex-shrink-0 bg-white text-black font-medium font-mono text-xs px-5 py-2 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-all hover:bg-neutral-100"
                      >
                        {sending ? "..." : "Send"}
                      </button>
                    </div>
                  </form>
                ) : (
                  <div className="border-t border-neutral-800/50 p-5 text-center">
                    <Link href="/login" className="text-xs font-mono text-violet-400 hover:text-violet-300 transition-colors bg-violet-400/10 border border-violet-400/20 px-4 py-2 rounded-lg inline-block">
                      Sign in to send messages
                    </Link>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function MemberRow({ member, index }: { member: TeamDetail["members"][number]; index: number }) {
  const inner = (
    <div
      className="member-card flex items-center gap-3 p-4 bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm hover:border-neutral-700/60 transition-all fade-up"
      style={{ animationDelay: `${0.15 + 0.04 * index}s` }}
    >
      <MemberAvatar name={member.name} type={member.member_type} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-white text-sm">{member.name}</span>
          {member.handle && (
            <span className="text-[10px] text-neutral-600 font-mono">@{member.handle}</span>
          )}
          <span className={`text-[10px] font-mono px-2 py-0.5 rounded-md font-medium ${
            member.role === "owner"
              ? "bg-orange-400/10 text-orange-400 border border-orange-400/20"
              : "bg-neutral-800/40 text-neutral-500 border border-neutral-700/30"
          }`}>{member.role}</span>
          <span className={`text-[10px] font-mono px-2 py-0.5 rounded-md ${
            member.member_type === "agent"
              ? "bg-cyan-400/10 text-cyan-400 border border-cyan-400/20"
              : "bg-violet-400/10 text-violet-400 border border-violet-400/20"
          }`}>{member.member_type}</span>
        </div>
      </div>
      <span className="text-[10px] font-mono text-neutral-700">{timeAgo(member.joined_at)}</span>
    </div>
  );

  if (member.member_type === "agent" && member.agent_id) {
    return <Link href={`/agents/${member.agent_id}`}>{inner}</Link>;
  }
  return inner;
}
