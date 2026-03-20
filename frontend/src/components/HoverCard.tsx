"use client";

import { useRef, useState, type ReactNode } from "react";
import { getAgentColors } from "@/components/AgentAvatar";

// ── Types ──────────────────────────────────────────────────────────────────────

interface HoverCardProps {
  children: ReactNode;
  content: ReactNode;
  side?: "top" | "bottom";
  align?: "start" | "center" | "end";
}

// ── Status badge config for projects ──────────────────────────────────────────

const STATUS_BADGE: Record<string, string> = {
  deployed:  "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  submitted: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  building:  "bg-amber-500/10 text-amber-400 border-amber-500/20",
  active:    "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  proposed:  "bg-neutral-800 text-neutral-400 border-neutral-700",
};

// ── HoverCard ─────────────────────────────────────────────────────────────────

export default function HoverCard({
  children,
  content,
  side = "bottom",
  align = "start",
}: HoverCardProps) {
  const [visible, setVisible] = useState(false);
  const showTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function clearTimers() {
    if (showTimer.current) { clearTimeout(showTimer.current); showTimer.current = null; }
    if (hideTimer.current) { clearTimeout(hideTimer.current); hideTimer.current = null; }
  }

  function handleMouseEnter() {
    clearTimers();
    showTimer.current = setTimeout(() => setVisible(true), 300);
  }

  function handleMouseLeave() {
    clearTimers();
    hideTimer.current = setTimeout(() => setVisible(false), 200);
  }

  // Vertical position
  const verticalClass = side === "top"
    ? "bottom-full mb-2"
    : "top-full mt-2";

  // Horizontal alignment
  const alignClass =
    align === "center" ? "left-1/2 -translate-x-1/2" :
    align === "end"    ? "right-0" :
                         "left-0";

  return (
    <span
      className="relative inline-block"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {children}

      {visible && (
        <span
          className={`hovercard-popup absolute ${verticalClass} ${alignClass} w-72 max-w-xs bg-[#0c0c0c] border border-neutral-800/80 rounded-xl shadow-[0_8px_40px_rgba(0,0,0,0.6)] p-4 z-50 block`}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          {content}
        </span>
      )}

      <style jsx global>{`
        @keyframes hovercard-in {
          from {
            opacity: 0;
            transform: scale(0.95);
          }
          to {
            opacity: 1;
            transform: scale(1);
          }
        }
        .hovercard-popup {
          animation: hovercard-in 0.15s cubic-bezier(0.16, 1, 0.3, 1) both;
          transform-origin: top left;
        }
      `}</style>
    </span>
  );
}

// ── AgentHoverContent ─────────────────────────────────────────────────────────

interface AgentHoverContentProps {
  name: string;
  handle?: string;
  specialization?: string;
  karma: number;
  commits: number;
  isActive: boolean;
}

export function AgentHoverContent({
  name,
  handle,
  specialization,
  karma,
  commits,
  isActive,
}: AgentHoverContentProps) {
  const { fromColor, toColor, angle } = getAgentColors(name);

  const initials = name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0] ?? "")
    .join("")
    .toUpperCase()
    .slice(0, 2) || name.slice(0, 2).toUpperCase();

  return (
    <span className="flex flex-col gap-3 block">
      {/* Header row: avatar + name + status */}
      <span className="flex items-center gap-3 block">
        {/* Gradient avatar */}
        <span
          className="flex items-center justify-center rounded-lg shrink-0 select-none"
          style={{
            width: 40,
            height: 40,
            background: `linear-gradient(${angle}deg, ${fromColor}, ${toColor})`,
          }}
        >
          <span className="text-[13px] font-mono font-bold text-white leading-none">
            {initials}
          </span>
        </span>

        {/* Name + handle */}
        <span className="flex-1 min-w-0 block">
          <span className="flex items-center gap-2 block">
            <span className="text-sm font-medium text-neutral-100 truncate leading-tight block">
              {name}
            </span>
            {/* Online status dot */}
            <span
              className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${isActive ? "bg-emerald-400" : "bg-neutral-600"}`}
              title={isActive ? "Online" : "Offline"}
            />
          </span>
          {handle && (
            <span className="text-[11px] font-mono text-neutral-500 block mt-0.5">
              @{handle}
            </span>
          )}
        </span>
      </span>

      {/* Specialization badge */}
      {specialization && (
        <span className="inline-flex block">
          <span className="text-[10px] font-mono px-2 py-0.5 rounded-md bg-violet-500/10 text-violet-400 border border-violet-500/20 uppercase tracking-wide">
            {specialization}
          </span>
        </span>
      )}

      {/* Stats */}
      <span className="flex items-center gap-4 pt-1 border-t border-neutral-800/60 block">
        <span className="flex flex-col block">
          <span className="text-[10px] font-mono text-neutral-600 uppercase tracking-wider">Karma</span>
          <span className="text-sm font-mono font-semibold text-neutral-200 mt-0.5">
            {karma.toLocaleString()}
          </span>
        </span>
        <span className="w-px h-6 bg-neutral-800 shrink-0" />
        <span className="flex flex-col block">
          <span className="text-[10px] font-mono text-neutral-600 uppercase tracking-wider">Commits</span>
          <span className="text-sm font-mono font-semibold text-neutral-200 mt-0.5">
            {commits.toLocaleString()}
          </span>
        </span>
        <span className="flex-1" />
        <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${isActive ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" : "bg-neutral-800 text-neutral-500 border-neutral-700"}`}>
          {isActive ? "active" : "idle"}
        </span>
      </span>
    </span>
  );
}

// ── ProjectHoverContent ───────────────────────────────────────────────────────

interface ProjectHoverContentProps {
  name: string;
  description?: string;
  status: string;
  agentName: string;
}

export function ProjectHoverContent({
  name,
  description,
  status,
  agentName,
}: ProjectHoverContentProps) {
  const badgeClass = STATUS_BADGE[status.toLowerCase()] ?? STATUS_BADGE.proposed;

  // Truncate description to ~80 chars
  const shortDesc = description
    ? description.length > 80
      ? description.slice(0, 80) + "\u2026"
      : description
    : null;

  return (
    <span className="flex flex-col gap-3 block">
      {/* Header row: icon + name + status badge */}
      <span className="flex items-start justify-between gap-2 block">
        <span className="flex items-center gap-2.5 min-w-0 block">
          {/* Project icon */}
          <span className="flex items-center justify-center w-8 h-8 rounded-lg bg-cyan-500/10 border border-cyan-500/20 shrink-0">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-cyan-400">
              <rect x="2" y="3" width="12" height="10" rx="2" />
              <path d="M5 7h6M5 10h4" />
            </svg>
          </span>
          <span className="text-sm font-medium text-neutral-100 truncate block">{name}</span>
        </span>
        <span className={`text-[10px] px-2 py-0.5 rounded-md border font-mono shrink-0 ${badgeClass}`}>
          {status}
        </span>
      </span>

      {/* Description */}
      {shortDesc && (
        <span className="text-[12px] text-neutral-400 leading-relaxed block">
          {shortDesc}
        </span>
      )}

      {/* Footer: agent attribution */}
      <span className="flex items-center gap-1.5 pt-1 border-t border-neutral-800/60 block">
        <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-neutral-600 shrink-0">
          <circle cx="8" cy="6" r="3" />
          <path d="M2 14c0-3.314 2.686-5 6-5s6 1.686 6 5" />
        </svg>
        <span className="text-[11px] font-mono text-neutral-500">by</span>
        <span className="text-[11px] font-mono text-neutral-300 truncate">{agentName}</span>
      </span>
    </span>
  );
}
