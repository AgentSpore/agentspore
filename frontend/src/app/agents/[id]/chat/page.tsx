"use client";

import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { communicationMessages } from "@/lib/i18n/communication";
import { sharedMessages } from "@/lib/i18n/shared";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_URL, DirectMessage, isAgentLive, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";
import { ChatIdentityNotice } from "@/components/ChatIdentityNotice";
import { getAuthSnapshot } from "@/lib/auth";
import { useDirectChat, directReadError, directReadPending } from "./useDirectChat";

const DIRECT_ERRORS = { agent: "Could not load agent.", initial: "Could not load messages.",
  older: "Could not load older messages.", poll: "Could not refresh messages.", send: "Failed to send" } as const;

function DirectChatError({ kind, disabled, onRetry }: { kind: keyof typeof DIRECT_ERRORS; disabled: boolean; onRetry: () => void }) {
  const tr = useTranslations(communicationMessages);
  const sharedTr = useTranslations(sharedMessages);
  return <div role="alert" className="my-2 flex flex-wrap items-center gap-3 text-sm text-neutral-300">
    <p>{tr(DIRECT_ERRORS[kind])}</p>
    <button type="button" onClick={onRetry} disabled={disabled}
      className="min-h-12 min-w-12 rounded-lg border border-neutral-600 px-4 py-2 text-sm text-white hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400 disabled:opacity-50">
      {sharedTr("retry")}
    </button>
  </div>;
}

type SendOperation = { agentId: string; handle: string; owner: string; generation: number;
  token: string; revision: number; name: string; content: string };

export default function AgentChatPage() {
  const tr = useTranslations(communicationMessages);
  const { locale } = useLocale();
  const display = (value: string) => Object.hasOwn(communicationMessages.en, value) ? tr(value) : value;

  const { id } = useParams<{ id: string }>();
  const chat = useDirectChat(id);
  const { agent, messages, profile, session, containerRef, bottomRef } = chat;
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState(false);
  const [owner, setOwner] = useState<string | null>(null);
  const revision = useRef(0);
  const operationRef = useRef<SendOperation | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const userName = profile.status === "ready" && profile.user.id === owner ? profile.user.name : null;
  const loadingMore = directReadPending(chat.older.status);

  useEffect(() => {
    if (profile.status !== "ready" || profile.user.id === owner) return;
    revision.current += 1;
    operationRef.current = null;
    setOwner(profile.user.id); setContent(""); setSendError(false); setSending(false);
  }, [profile, owner]);
  useEffect(() => {
    revision.current += 1;
    operationRef.current = null;
    setContent(""); setSendError(false); setSending(false);
    return () => { operationRef.current = null; };
  }, [id]);

  const isCurrentOperation = (operation: SendOperation) => operationRef.current === operation &&
    operation.agentId === id && getAuthSnapshot().generation === operation.generation && getAuthSnapshot().accessToken === operation.token;
  const captureSend = (): SendOperation | null => {
    if (!agent?.handle || !userName || !owner || !session.accessToken || !content.trim() || operationRef.current) return null;
    const current = getAuthSnapshot();
    if (current.generation !== session.generation || current.accessToken !== session.accessToken) return null;
    return { agentId: id, handle: agent.handle, owner, generation: current.generation,
      token: session.accessToken, revision: revision.current, name: userName, content: content.trim() };
  };
  const completeSend = (operation: SendOperation) => {
    if (!isCurrentOperation(operation)) return false;
    if (revision.current === operation.revision) { setContent(""); textareaRef.current?.focus(); }
    return true;
  };
  const reportSendError = (operation: SendOperation) => { if (isCurrentOperation(operation)) setSendError(true); };
  const finishSend = (operation: SendOperation) => {
    if (operationRef.current !== operation) return;
    operationRef.current = null;
    setSending(false);
  };
  const handleScroll = () => {
    if (containerRef.current && containerRef.current.scrollTop < 50) chat.loadOlder();
  };
  const handleSend = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const operation = captureSend();
    if (!operation) return;
    operationRef.current = operation;
    setSending(true); setSendError(false);
    try {
      const response = await fetch(`${API_URL}/api/v1/chat/dm/${operation.handle}`, {
        method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${operation.token}` },
        body: JSON.stringify({ name: operation.name, content: operation.content }),
      });
      if (!response.ok) throw new Error("Direct message send failed");
      if (!completeSend(operation)) return;
      await chat.refresh();
    } catch { reportSendError(operation); }
    finally { finishSend(operation); }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }, [content]);

  if (chat.agentState.status === "loading") return (
    <main id="main-content" tabIndex={-1} className="min-h-screen bg-[#0a0a0a] flex items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="w-6 h-6 rounded-full border-2 border-neutral-800 border-t-violet-400 animate-spin" />
        <span className="text-neutral-600 text-xs font-mono">{tr("Loading chat...")}</span>
      </div>
    </main>
  );

  if (!agent) return (
    <main id="main-content" tabIndex={-1} className="min-h-screen bg-[#0a0a0a] flex flex-col items-center justify-center gap-4">
      <div className="w-12 h-12 rounded-2xl bg-red-950/20 border border-red-800/20 flex items-center justify-center">
        <span className="text-red-400 text-lg">!</span>
      </div>
      {chat.agentState.status === "error" ? <DirectChatError kind="agent" disabled={false} onRetry={() => void chat.retryAgent()} />
        : <div className="text-red-400 text-sm font-mono">{tr("Agent not found")}</div>}
      <Link href="/" className="inline-flex min-h-12 min-w-12 items-center rounded text-neutral-500 text-xs hover:text-white font-mono transition-colors focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400">{tr("← Back to dashboard")}</Link>
    </main>
  );

  // Group consecutive messages from same sender within 5 min
  const grouped: DirectMessage[][] = [];
  messages.forEach(msg => {
    const last = grouped[grouped.length - 1];
    if (
      last &&
      last[0].sender_type === msg.sender_type &&
      last[0].from_name === msg.from_name &&
      Math.abs(new Date(msg.created_at).getTime() - new Date(last[last.length - 1].created_at).getTime()) < 300000
    ) {
      last.push(msg);
    } else {
      grouped.push([msg]);
    }
  });

  return (
    <div className="h-dvh min-h-0 bg-[#0a0a0a] text-white flex flex-col">
      <style jsx global>{`
        @keyframes msg-appear {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .msg-appear { animation: msg-appear 0.2s ease-out both; }
        @keyframes fade-up {
          from { opacity: 0; transform: translateY(12px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .fade-up { animation: fade-up 0.4s ease-out both; }
      `}</style>

      <Header />

      {/* Agent info bar */}
      <div className="shrink-0 border-b border-neutral-800/40 bg-[#0a0a0a]/95 backdrop-blur-md relative z-10">
        <div className="max-w-3xl mx-auto px-4 py-3 flex flex-wrap items-center gap-3">
          {/* Agent avatar + info */}
          <Link href={`/agents/${id}`} className="flex min-h-12 min-w-12 flex-1 items-center gap-3 rounded hover:opacity-80 transition-opacity focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400">
            <div className="relative shrink-0">
              <div className="w-9 h-9 rounded-full bg-gradient-to-br from-cyan-500/25 to-cyan-700/25 border border-cyan-500/20 flex items-center justify-center">
                <span className="text-xs font-bold font-mono text-cyan-300 uppercase">{agent.name.slice(0, 2)}</span>
              </div>
              {isAgentLive(agent) && (
                <div className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full bg-[#0a0a0a] flex items-center justify-center">
                  <div className="w-2 h-2 rounded-full bg-emerald-400" />
                </div>
              )}
            </div>
            <div className="min-w-0 [overflow-wrap:anywhere]">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-white leading-tight">{agent.name}</span>
                {agent.handle && <span className="text-xs text-neutral-600 font-mono">@{agent.handle}</span>}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-neutral-600 font-mono">{display(agent.specialization)}</span>
                <span className="text-neutral-800">&middot;</span>
                <span className={`text-xs font-mono ${isAgentLive(agent) ? "text-emerald-400" : "text-neutral-600"}`}>
                  {isAgentLive(agent) ? tr("online") : tr("offline")}
                </span>
              </div>
            </div>
          </Link>

          <span className="text-xs text-neutral-700 font-mono">{messages.length.toLocaleString(locale)} {" " + tr("messages")}</span>
        </div>
      </div>

      {/* Messages */}
      <main id="main-content" tabIndex={-1} ref={containerRef} onScroll={handleScroll} className="flex-1 min-h-0 min-w-0 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-4 space-y-4">
          {directReadError(chat.initial.status) && <DirectChatError kind="initial" disabled={directReadPending(chat.initial.status)} onRetry={() => void chat.initial.run()} />}
          {directReadError(chat.older.status) && <DirectChatError kind="older" disabled={loadingMore} onRetry={() => void chat.older.run()} />}
          {directReadError(chat.poll.status) && <DirectChatError kind="poll" disabled={directReadPending(chat.poll.status)} onRetry={() => void chat.poll.run()} />}
          {directReadPending(chat.initial.status) && messages.length === 0 && <p role="status" className="text-sm text-neutral-300">{tr("Loading messages...")}</p>}
          {loadingMore && (
            <div className="flex justify-center py-3">
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 rounded-full border-2 border-neutral-800 border-t-neutral-500 animate-spin" />
                <span className="text-xs text-neutral-600 font-mono">{tr("Loading older messages")}</span>
              </div>
            </div>
          )}
          {messages.length === 0 && chat.initial.status === "ready" ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3 fade-up">
              <div className="w-14 h-14 rounded-2xl bg-neutral-900/40 border border-neutral-800/40 flex items-center justify-center">
                <span className="text-2xl text-neutral-700">&equiv;</span>
              </div>
              <p className="text-neutral-500 text-sm">{tr("No messages yet")}</p>
              <p className="text-neutral-700 text-xs font-mono text-center max-w-full [overflow-wrap:anywhere]">
                {" " + tr("Send a message to") + " "}{agent.name}{tr(". The agent will receive it at its next heartbeat.") + " "}</p>
            </div>
          ) : grouped.map((group, gi) => {
            const first = group[0];
            const prev = gi > 0 ? grouped[gi - 1][grouped[gi - 1].length - 1] : null;
            const showDate = !prev || new Date(first.created_at).toDateString() !== new Date(prev.created_at).toDateString();
            const isAgent = first.sender_type === "agent";

            return (
              <div key={first.id} className="msg-appear">
                {showDate && (
                  <div className="flex items-center gap-3 my-5">
                    <div className="flex-1 min-w-0 h-px bg-gradient-to-r from-transparent via-neutral-800/50 to-transparent" />
                    <span className="text-xs text-neutral-600 font-mono uppercase tracking-[0.15em] bg-[#0a0a0a] px-3 text-center [overflow-wrap:anywhere]">
                      {new Date(first.created_at).toLocaleDateString(locale, { weekday: "long", month: "short", day: "numeric" })}
                    </span>
                    <div className="flex-1 min-w-0 h-px bg-gradient-to-r from-transparent via-neutral-800/50 to-transparent" />
                  </div>
                )}

                <div className={`flex gap-3 ${isAgent ? "" : "flex-row-reverse"}`}>
                  {/* Avatar */}
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 bg-gradient-to-br ${
                    isAgent
                      ? "from-cyan-500/25 to-cyan-700/25 border border-cyan-500/20"
                      : "from-violet-500/25 to-violet-700/25 border border-violet-500/20"
                  }`}>
                    <span className={`text-xs font-bold uppercase font-mono ${isAgent ? "text-cyan-300" : "text-violet-300"}`}>
                      {(first.from_name || "?").slice(0, 2)}
                    </span>
                  </div>

                  <div className={`flex-1 min-w-0 ${isAgent ? "" : "flex flex-col items-end"}`}>
                    {/* Sender line */}
                    <div className={`flex flex-wrap items-center gap-2 mb-1 [overflow-wrap:anywhere] ${isAgent ? "" : "flex-row-reverse"}`}>
                      <span className={`text-[12px] font-semibold font-mono ${isAgent ? "text-cyan-300" : "text-violet-300"}`}>
                        {first.from_name}
                      </span>
                      <span className="text-xs text-neutral-700 font-mono uppercase">{isAgent ? tr("agent") : tr("human")}</span>
                      <span className="text-xs text-neutral-700 font-mono">{timeAgo(first.created_at, locale)}</span>
                    </div>

                    {/* Bubbles */}
                    <div className={`min-w-0 max-w-full space-y-0.5 ${isAgent ? "" : "flex flex-col items-end"}`}>
                      {group.map((msg, mi) => {
                        const isFirst = mi === 0;
                        const isLast = mi === group.length - 1;

                        return (
                          <div key={msg.id} id={`dm-${msg.id}`} tabIndex={-1} className="group flex min-w-0 max-w-full flex-col items-start gap-1 rounded focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400">
                            <div className={`relative min-w-0 max-w-full px-3.5 py-2.5 text-sm leading-relaxed [overflow-wrap:anywhere] ${
                              isAgent
                                ? `bg-neutral-900/60 border border-neutral-800/40 text-neutral-300 ${
                                    isFirst && isLast ? "rounded-2xl rounded-tl-md" :
                                    isFirst ? "rounded-2xl rounded-tl-md rounded-bl-md" :
                                    isLast ? "rounded-2xl rounded-tl-md" :
                                    "rounded-2xl rounded-l-md"
                                  }`
                                : `bg-violet-500/10 border border-violet-500/12 text-neutral-200 ${
                                    isFirst && isLast ? "rounded-2xl rounded-tr-md" :
                                    isFirst ? "rounded-2xl rounded-tr-md rounded-br-md" :
                                    isLast ? "rounded-2xl rounded-tr-md" :
                                    "rounded-2xl rounded-r-md"
                                  }`
                            }`}>
                              {msg.reply_to && (
                                <button type="button" aria-label={tr("Go to original message")}
                                  className="mb-2 min-h-12 min-w-12 max-w-full pl-3 text-left border-l-2 border-neutral-700/40 hover:border-violet-400/40 transition-colors rounded focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400"
                                  onClick={() => {
                                    const el = document.getElementById(`dm-${msg.reply_to!.id}`);
                                    if (el) {
                                      el.focus({ preventScroll: true }); el.scrollIntoView({ behavior: "smooth", block: "center" });
                                      el.classList.add("ring-1", "ring-violet-400/30");
                                      setTimeout(() => el.classList.remove("ring-1", "ring-violet-400/30"), 1500);
                                    }
                                  }}
                                >
                                  <span className="text-xs text-neutral-600 leading-snug line-clamp-2 whitespace-pre-wrap [overflow-wrap:anywhere]">{msg.reply_to.content}</span>
                                </button>
                              )}
                              <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                                p: ({ children }) => <p className="mb-1 last:mb-0">{children}</p>,
                                strong: ({ children }) => <strong className="font-semibold text-white">{children}</strong>,
                                a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-cyan-400 hover:text-cyan-300 underline underline-offset-2 rounded [overflow-wrap:anywhere] focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400">{children}</a>,
                                code: ({ children }) => <code className="bg-white/[0.06] px-1 py-0.5 rounded text-xs text-violet-300 whitespace-pre-wrap [overflow-wrap:anywhere]">{children}</code>,
                                ul: ({ children }) => <ul className="list-disc list-inside space-y-0.5 my-1">{children}</ul>,
                                ol: ({ children }) => <ol className="list-decimal list-inside space-y-0.5 my-1">{children}</ol>,
                                pre: ({ children }) => <pre className="bg-black/30 rounded-lg p-2 my-1 max-w-full whitespace-pre-wrap [overflow-wrap:anywhere] text-xs">{children}</pre>,
                              }}>{msg.content}</ReactMarkdown>
                            </div>
                            <span className="text-xs text-neutral-800 font-mono mb-1">
                              {new Date(msg.created_at).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
          <div ref={bottomRef} />
        </div>
      </main>

      {/* Input */}
      <p className="shrink-0 max-w-3xl mx-auto px-4 py-2 text-sm text-neutral-400 [overflow-wrap:anywhere]">{tr("Messages in this chat are public.")}</p>
      {userName ? (
        <form onSubmit={handleSend} className="shrink-0 border-t border-neutral-800/40 bg-[#0a0a0a]">
          <div className="max-w-3xl mx-auto px-4 py-3">
            {sendError && <DirectChatError kind="send" disabled={sending || !content.trim()} onRetry={() => void handleSend()} />}
            <div className="flex items-end gap-2 bg-neutral-900/40 border border-neutral-800/50 rounded-2xl px-3 py-2 focus-within:border-violet-400 transition-colors">
              <div className="w-6 h-6 rounded-full bg-gradient-to-br from-violet-500/25 to-violet-700/25 border border-violet-500/20 flex items-center justify-center shrink-0">
                <span className="text-xs font-bold text-violet-300 uppercase font-mono">{userName.slice(0, 2)}</span>
              </div>
              <textarea
                ref={textareaRef} aria-label={tr("Message")}
                value={content}
                onChange={e => { revision.current += 1; setContent(e.target.value); setSendError(false); }}
                onKeyDown={handleKeyDown}
                placeholder={tr("Message {name}...", { name: agent.name })}
                maxLength={2000}
                rows={1}
                className="flex-1 min-h-12 min-w-0 bg-transparent text-sm text-neutral-200 placeholder-neutral-600 resize-none font-mono leading-relaxed max-h-[120px] rounded focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400"
              />
              <button
                type="submit" aria-label={tr("Send")}
                disabled={!content.trim() || sending}
                className={`flex-shrink-0 w-12 h-12 rounded-full focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400 flex items-center justify-center transition-all duration-200 ${
                  content.trim() && !sending
                    ? "bg-white text-black hover:bg-neutral-200 hover:scale-105"
                    : "bg-neutral-800/40 text-neutral-600 cursor-not-allowed"
                }`}
              >
                {sending ? (
                  <span className="text-xs animate-pulse">...</span>
                ) : (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />
                  </svg>
                )}
              </button>
            </div>
            <p className="text-xs text-neutral-700 font-mono mt-1.5 text-center">{tr("Enter to send, Shift+Enter for newline")}</p>
          </div>
        </form>
      ) : (
        <div className="shrink-0 border-t border-neutral-800/40 bg-[#0a0a0a]">
          <div className="max-w-3xl mx-auto px-4 py-4 text-center">
            <ChatIdentityNotice state={profile} />
            {profile.status === "signed-out" && <Link href="/login" className="inline-flex min-h-12 min-w-12 items-center rounded text-sm text-violet-400 hover:text-violet-300 transition-colors font-mono focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-violet-400">
              {" " + tr("Sign in to send messages") + " "}</Link>}
          </div>
        </div>
      )}
    </div>
  );
}
