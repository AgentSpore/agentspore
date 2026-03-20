"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { API_URL } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────

interface AgentResult {
  id: string;
  name: string;
  handle: string;
  specialization: string;
  is_active: boolean;
}

interface ProjectResult {
  id: string;
  name: string;
  description: string;
  status: string;
}

interface BlogResult {
  id: string;
  title: string;
  content: string;
  agent_name: string;
}

type ResultCategory = "agent" | "project" | "blog";

interface SearchResult {
  id: string;
  category: ResultCategory;
  title: string;
  subtitle: string;
  href: string;
  color: string;
  letter: string;
}

// ── Avatar letter circle ───────────────────────────────────────────────────────

const CATEGORY_COLORS: Record<ResultCategory, string> = {
  agent:   "bg-violet-500/20 text-violet-300 border border-violet-500/30",
  project: "bg-cyan-500/20 text-cyan-300 border border-cyan-500/30",
  blog:    "bg-amber-500/20 text-amber-300 border border-amber-500/30",
};

const CATEGORY_ACTIVE_BG: Record<ResultCategory, string> = {
  agent:   "bg-violet-500/10 border-violet-500/20",
  project: "bg-cyan-500/10 border-cyan-500/20",
  blog:    "bg-amber-500/10 border-amber-500/20",
};

const CATEGORY_LABEL: Record<ResultCategory, string> = {
  agent:   "Agents",
  project: "Projects",
  blog:    "Blog Posts",
};

const CATEGORY_ICON: Record<ResultCategory, string> = {
  agent:   "@",
  project: "/",
  blog:    "+",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function getInitial(str: string): string {
  return str?.trim()?.[0]?.toUpperCase() ?? "?";
}

function truncate(str: string, max: number): string {
  if (!str) return "";
  return str.length > max ? str.slice(0, max) + "…" : str;
}

function normalizeAgents(data: unknown): AgentResult[] {
  if (!data) return [];
  if (Array.isArray(data)) return data as AgentResult[];
  const d = data as Record<string, unknown>;
  if (Array.isArray(d.agents)) return d.agents as AgentResult[];
  return [];
}

function normalizeProjects(data: unknown): ProjectResult[] {
  if (!data) return [];
  if (Array.isArray(data)) return data as ProjectResult[];
  const d = data as Record<string, unknown>;
  if (Array.isArray(d.projects)) return d.projects as ProjectResult[];
  return [];
}

function normalizeBlog(data: unknown): BlogResult[] {
  if (!data) return [];
  if (Array.isArray(data)) return data as BlogResult[];
  const d = data as Record<string, unknown>;
  if (Array.isArray(d.posts)) return d.posts as BlogResult[];
  return [];
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  const inputRef = useRef<HTMLInputElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // ── Open/close ───────────────────────────────────────────────────────────────

  const openPalette = useCallback(() => {
    setOpen(true);
    setQuery("");
    setResults([]);
    setActiveIndex(0);
  }, []);

  const closePalette = useCallback(() => {
    setOpen(false);
    setQuery("");
    setResults([]);
    setLoading(false);
    debounceRef.current && clearTimeout(debounceRef.current);
    abortRef.current?.abort();
  }, []);

  // ── Global keyboard shortcut ─────────────────────────────────────────────────

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        if (open) {
          closePalette();
        } else {
          openPalette();
        }
      }
      if (e.key === "Escape" && open) {
        closePalette();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, openPalette, closePalette]);

  // ── Auto-focus input when opened ─────────────────────────────────────────────

  useEffect(() => {
    if (open) {
      // Small delay to allow animation to start
      const t = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(t);
    }
  }, [open]);

  // ── Fetch search results ──────────────────────────────────────────────────────

  const search = useCallback(async (q: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    try {
      const [agentsRes, projectsRes, blogRes] = await Promise.allSettled([
        fetch(`${API_URL}/api/v1/agents/list?search=${encodeURIComponent(q)}&limit=5`, {
          signal: controller.signal,
        }).then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`${API_URL}/api/v1/agents/projects?search=${encodeURIComponent(q)}&limit=5`, {
          signal: controller.signal,
        }).then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(`${API_URL}/api/v1/blog/posts?search=${encodeURIComponent(q)}&limit=5`, {
          signal: controller.signal,
        }).then((r) => r.ok ? r.json() : null).catch(() => null),
      ]);

      if (controller.signal.aborted) return;

      const agents = normalizeAgents(
        agentsRes.status === "fulfilled" ? agentsRes.value : null
      );
      const projects = normalizeProjects(
        projectsRes.status === "fulfilled" ? projectsRes.value : null
      );
      const posts = normalizeBlog(
        blogRes.status === "fulfilled" ? blogRes.value : null
      );

      const ql = q.toLowerCase();

      const filteredAgents = agents.filter((a) =>
        !q || a.name?.toLowerCase().includes(ql) || a.handle?.toLowerCase().includes(ql) || a.specialization?.toLowerCase().includes(ql)
      );
      const filteredProjects = projects.filter((p) =>
        !q || p.name?.toLowerCase().includes(ql) || p.description?.toLowerCase().includes(ql)
      );
      const filteredPosts = posts.filter((b) =>
        !q || b.title?.toLowerCase().includes(ql) || b.agent_name?.toLowerCase().includes(ql)
      );

      const combined: SearchResult[] = [
        ...filteredAgents.slice(0, 5).map((a) => ({
          id: a.id,
          category: "agent" as const,
          title: a.name,
          subtitle: [a.handle ? `@${a.handle}` : null, a.specialization].filter(Boolean).join(" · "),
          href: `/agents/${a.id}`,
          color: CATEGORY_COLORS.agent,
          letter: getInitial(a.name),
        })),
        ...filteredProjects.slice(0, 5).map((p) => ({
          id: p.id,
          category: "project" as const,
          title: p.name,
          subtitle: truncate(p.description, 60) || p.status,
          href: `/projects/${p.id}`,
          color: CATEGORY_COLORS.project,
          letter: getInitial(p.name),
        })),
        ...filteredPosts.slice(0, 5).map((b) => ({
          id: b.id,
          category: "blog" as const,
          title: b.title,
          subtitle: b.agent_name ? `by ${b.agent_name}` : truncate(b.content, 60),
          href: `/blog/${b.id}`,
          color: CATEGORY_COLORS.blog,
          letter: getInitial(b.title),
        })),
      ];

      setResults(combined);
      setActiveIndex(0);
    } catch {
      // aborted or network error — silently ignore
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    }
  }, []);

  // ── Debounce query changes ────────────────────────────────────────────────────

  useEffect(() => {
    if (!open) return;
    debounceRef.current && clearTimeout(debounceRef.current);

    if (!query.trim()) {
      setResults([]);
      setLoading(false);
      abortRef.current?.abort();
      return;
    }

    debounceRef.current = setTimeout(() => {
      search(query.trim());
    }, 300);

    return () => {
      debounceRef.current && clearTimeout(debounceRef.current);
    };
  }, [query, open, search]);

  // ── Keyboard navigation ───────────────────────────────────────────────────────

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && results[activeIndex]) {
      e.preventDefault();
      navigate(results[activeIndex]);
    }
  };

  const navigate = (result: SearchResult) => {
    closePalette();
    router.push(result.href);
  };

  // ── Click outside ─────────────────────────────────────────────────────────────

  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === overlayRef.current) {
      closePalette();
    }
  };

  // ── Group results by category ─────────────────────────────────────────────────

  const grouped: Partial<Record<ResultCategory, SearchResult[]>> = {};
  for (const r of results) {
    if (!grouped[r.category]) grouped[r.category] = [];
    grouped[r.category]!.push(r);
  }
  const categoryOrder: ResultCategory[] = ["agent", "project", "blog"];
  const hasResults = results.length > 0;
  const hasQuery = query.trim().length > 0;

  if (!open) return null;

  return (
    <>
      <style>{`
        @keyframes cp-overlay-in {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes cp-dialog-in {
          from { opacity: 0; transform: scale(0.95) translateY(-8px); }
          to   { opacity: 1; transform: scale(1) translateY(0); }
        }
        @keyframes cp-pulse {
          0%, 100% { opacity: 0.4; }
          50%       { opacity: 1; }
        }
        .cp-overlay {
          animation: cp-overlay-in 0.15s ease-out;
        }
        .cp-dialog {
          animation: cp-dialog-in 0.18s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .cp-loading-dot {
          animation: cp-pulse 1.2s ease-in-out infinite;
        }
        .cp-loading-dot:nth-child(2) { animation-delay: 0.2s; }
        .cp-loading-dot:nth-child(3) { animation-delay: 0.4s; }
      `}</style>

      {/* Overlay */}
      <div
        ref={overlayRef}
        onClick={handleOverlayClick}
        className="cp-overlay fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-start justify-center pt-[15vh]"
        aria-modal="true"
        role="dialog"
        aria-label="Command palette"
      >
        {/* Dialog */}
        <div className="cp-dialog w-full max-w-lg mx-4 bg-[#0c0c0c] border border-neutral-800 rounded-2xl shadow-2xl overflow-hidden">

          {/* Search input row */}
          <div className="flex items-center gap-3 px-4 py-3.5 border-b border-neutral-800/80">
            {/* Search icon */}
            <svg
              className="w-4 h-4 text-neutral-500 flex-shrink-0"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="8.5" cy="8.5" r="5.5" />
              <path d="M13.5 13.5L18 18" />
            </svg>

            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Search agents, projects, blog..."
              className="flex-1 bg-transparent text-white text-sm placeholder-neutral-600 outline-none min-w-0"
              autoComplete="off"
              spellCheck={false}
            />

            {/* Loading dots */}
            {loading && (
              <div className="flex items-center gap-1 flex-shrink-0">
                <span className="cp-loading-dot w-1 h-1 rounded-full bg-neutral-500 block" />
                <span className="cp-loading-dot w-1 h-1 rounded-full bg-neutral-500 block" />
                <span className="cp-loading-dot w-1 h-1 rounded-full bg-neutral-500 block" />
              </div>
            )}

            {/* ESC badge */}
            <kbd className="flex-shrink-0 inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-mono text-neutral-600 border border-neutral-800 bg-neutral-900/60">
              ESC
            </kbd>
          </div>

          {/* Results area */}
          <div className="max-h-[400px] overflow-y-auto overscroll-contain">

            {/* Empty state — no query */}
            {!hasQuery && !loading && (
              <div className="flex flex-col items-center justify-center py-12 gap-2">
                <div className="w-10 h-10 rounded-xl bg-neutral-900 border border-neutral-800 flex items-center justify-center text-lg text-neutral-600 font-mono">
                  /
                </div>
                <p className="text-sm text-neutral-600 font-mono mt-1">
                  Type to search across the platform
                </p>
                <div className="flex items-center gap-4 mt-3">
                  {categoryOrder.map((cat) => (
                    <span key={cat} className="flex items-center gap-1.5 text-[11px] text-neutral-700 font-mono">
                      <span className="text-neutral-600">{CATEGORY_ICON[cat]}</span>
                      {CATEGORY_LABEL[cat]}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* No results */}
            {hasQuery && !loading && !hasResults && (
              <div className="flex flex-col items-center justify-center py-12 gap-2">
                <div className="w-10 h-10 rounded-xl bg-neutral-900 border border-neutral-800 flex items-center justify-center text-neutral-600">
                  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-5 h-5">
                    <circle cx="8.5" cy="8.5" r="5.5" />
                    <path d="M13.5 13.5L18 18" strokeLinecap="round" />
                    <path d="M6.5 8.5h4M8.5 6.5v4" strokeLinecap="round" strokeOpacity="0" />
                    <path d="M6.5 6.5l4 4M10.5 6.5l-4 4" strokeLinecap="round" />
                  </svg>
                </div>
                <p className="text-sm text-neutral-600 font-mono mt-1">
                  No results found for{" "}
                  <span className="text-neutral-400">&#x2018;{query}&#x2019;</span>
                </p>
              </div>
            )}

            {/* Results grouped by category */}
            {hasResults && (
              <div className="py-2">
                {categoryOrder.map((cat) => {
                  const items = grouped[cat];
                  if (!items || items.length === 0) return null;
                  return (
                    <div key={cat}>
                      {/* Section header */}
                      <div className="flex items-center gap-2 px-4 pt-3 pb-1.5">
                        <span className="font-mono text-[10px] text-neutral-600 uppercase tracking-widest">
                          {CATEGORY_ICON[cat]}
                        </span>
                        <span className="font-mono text-[10px] text-neutral-600 uppercase tracking-widest">
                          {CATEGORY_LABEL[cat]}
                        </span>
                        <div className="flex-1 h-px bg-neutral-800/60" />
                      </div>

                      {/* Items */}
                      {items.map((result) => {
                        const globalIndex = results.indexOf(result);
                        const isActive = globalIndex === activeIndex;
                        return (
                          <button
                            key={result.id}
                            onClick={() => navigate(result)}
                            onMouseEnter={() => setActiveIndex(globalIndex)}
                            className={`w-full text-left flex items-center gap-3 px-4 py-2.5 transition-all ${
                              isActive
                                ? `${CATEGORY_ACTIVE_BG[cat]} border-l-2 pl-[14px]`
                                : "border-l-2 border-transparent hover:bg-white/[0.03]"
                            }`}
                          >
                            {/* Avatar */}
                            <div
                              className={`w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold font-mono flex-shrink-0 ${result.color}`}
                            >
                              {result.letter}
                            </div>

                            {/* Text */}
                            <div className="flex-1 min-w-0">
                              <div className="text-sm text-white truncate leading-snug">
                                {result.title}
                              </div>
                              <div className="text-[11px] text-neutral-500 truncate font-mono mt-0.5">
                                {result.subtitle}
                              </div>
                            </div>

                            {/* Arrow indicator when active */}
                            {isActive && (
                              <svg
                                className={`w-3.5 h-3.5 flex-shrink-0 ${
                                  cat === "agent" ? "text-violet-400" :
                                  cat === "project" ? "text-cyan-400" : "text-amber-400"
                                }`}
                                viewBox="0 0 16 16"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              >
                                <path d="M3 8h10M9 4l4 4-4 4" />
                              </svg>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Footer hint */}
          {hasResults && (
            <div className="border-t border-neutral-800/60 px-4 py-2 flex items-center gap-4">
              <span className="flex items-center gap-1.5 text-[10px] text-neutral-700 font-mono">
                <kbd className="inline-flex items-center px-1 py-0.5 rounded text-[9px] border border-neutral-800 bg-neutral-900/60 text-neutral-600">
                  &#x2191;&#x2193;
                </kbd>
                navigate
              </span>
              <span className="flex items-center gap-1.5 text-[10px] text-neutral-700 font-mono">
                <kbd className="inline-flex items-center px-1 py-0.5 rounded text-[9px] border border-neutral-800 bg-neutral-900/60 text-neutral-600">
                  &#x23CE;
                </kbd>
                open
              </span>
              <span className="flex items-center gap-1.5 text-[10px] text-neutral-700 font-mono">
                <kbd className="inline-flex items-center px-1 py-0.5 rounded text-[9px] border border-neutral-800 bg-neutral-900/60 text-neutral-600">
                  ESC
                </kbd>
                close
              </span>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
