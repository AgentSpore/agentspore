"use client";

import { useMemo, useState } from "react";
import ReactDiffViewer, { DiffMethod } from "react-diff-viewer-continued";

export interface DiffFileChange {
  path: string;
  status: string;
  patch: string;
}

const STATUS_COLOR: Record<string, string> = {
  added: "text-emerald-300 bg-emerald-400/10 border-emerald-400/20",
  modified: "text-cyan-300 bg-cyan-400/10 border-cyan-400/20",
  deleted: "text-red-300 bg-red-400/10 border-red-400/20",
  renamed: "text-violet-300 bg-violet-400/10 border-violet-400/20",
};

function reconstructFiles(patch: string): { oldContent: string; newContent: string } {
  const lines = patch.split("\n");
  const oldLines: string[] = [];
  const newLines: string[] = [];
  let inHunk = false;
  for (const ln of lines) {
    if (ln.startsWith("@@")) { inHunk = true; continue; }
    if (!inHunk) continue;
    if (ln.startsWith("+++") || ln.startsWith("---")) continue;
    if (ln.startsWith("+")) { newLines.push(ln.slice(1)); continue; }
    if (ln.startsWith("-")) { oldLines.push(ln.slice(1)); continue; }
    if (ln.startsWith(" ")) {
      const body = ln.slice(1);
      oldLines.push(body);
      newLines.push(body);
      continue;
    }
    if (ln.startsWith("\\")) continue;
  }
  return { oldContent: oldLines.join("\n"), newContent: newLines.join("\n") };
}

function countStats(patch: string): { add: number; del: number } {
  let add = 0, del = 0;
  for (const ln of patch.split("\n")) {
    if (ln.startsWith("+") && !ln.startsWith("+++")) add++;
    else if (ln.startsWith("-") && !ln.startsWith("---")) del++;
  }
  return { add, del };
}

const DIFF_STYLES = {
  variables: {
    dark: {
      diffViewerBackground: "#0a0a0a",
      diffViewerColor: "#e5e7eb",
      addedBackground: "rgba(16, 185, 129, 0.12)",
      addedColor: "#d1fae5",
      removedBackground: "rgba(239, 68, 68, 0.12)",
      removedColor: "#fecaca",
      wordAddedBackground: "rgba(16, 185, 129, 0.35)",
      wordRemovedBackground: "rgba(239, 68, 68, 0.35)",
      addedGutterBackground: "rgba(16, 185, 129, 0.18)",
      removedGutterBackground: "rgba(239, 68, 68, 0.18)",
      gutterBackground: "#0f0f0f",
      gutterBackgroundDark: "#0a0a0a",
      highlightBackground: "rgba(139, 92, 246, 0.10)",
      highlightGutterBackground: "rgba(139, 92, 246, 0.18)",
      codeFoldGutterBackground: "#141414",
      codeFoldBackground: "#141414",
      emptyLineBackground: "#0a0a0a",
      gutterColor: "#6b7280",
      addedGutterColor: "#6ee7b7",
      removedGutterColor: "#fca5a5",
      codeFoldContentColor: "#a78bfa",
      diffViewerTitleBackground: "#0f0f0f",
      diffViewerTitleColor: "#e5e7eb",
      diffViewerTitleBorderColor: "#262626",
    },
  },
  contentText: { fontSize: "12px", lineHeight: "1.55", fontFamily: "Menlo, Consolas, monospace" } as const,
  gutter: { padding: "0 8px", minWidth: "32px", fontSize: "11px" } as const,
  line: { minHeight: "18px" } as const,
};

function DiffFileCard({ file, splitView, viewed, onToggleViewed }: {
  file: DiffFileChange;
  splitView: boolean;
  viewed: boolean;
  onToggleViewed: () => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [copied, setCopied] = useState(false);
  const { oldContent, newContent } = useMemo(() => reconstructFiles(file.patch), [file.patch]);
  const stats = useMemo(() => countStats(file.patch), [file.patch]);
  const statusCls = STATUS_COLOR[file.status] ?? "text-neutral-300 bg-neutral-400/10 border-neutral-400/20";

  const copy = async () => {
    await navigator.clipboard.writeText(file.patch);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div className={`rounded-xl border ${viewed ? "border-neutral-900 bg-white/[0.01] opacity-60" : "border-neutral-800/60 bg-white/[0.02]"} overflow-hidden`}>
      <div className="flex items-center justify-between gap-3 px-3.5 py-2 bg-neutral-950/80 border-b border-neutral-800/60 text-xs font-mono">
        <div className="flex items-center gap-2.5 min-w-0 flex-1">
          <button onClick={() => setCollapsed(!collapsed)} className="text-neutral-500 hover:text-neutral-200 transition">
            {collapsed ? "▸" : "▾"}
          </button>
          <span className={`px-1.5 py-0.5 rounded border text-[10px] uppercase tracking-wider ${statusCls}`}>
            {file.status}
          </span>
          <span className="text-neutral-200 truncate">{file.path}</span>
          <span className="flex items-center gap-1.5 text-[11px] shrink-0">
            <span className="text-emerald-400">+{stats.add}</span>
            <span className="text-red-400">−{stats.del}</span>
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={copy} className="px-2 py-0.5 rounded text-[11px] border border-neutral-800 text-neutral-400 hover:text-neutral-100 hover:border-neutral-600 transition">
            {copied ? "Copied" : "Copy"}
          </button>
          <label className="flex items-center gap-1.5 text-[11px] text-neutral-400 cursor-pointer select-none">
            <input type="checkbox" checked={viewed} onChange={onToggleViewed} className="accent-violet-500 w-3 h-3" />
            Viewed
          </label>
        </div>
      </div>

      {!collapsed && (
        <div className="diff-viewer-wrap overflow-x-auto">
          <ReactDiffViewer
            oldValue={oldContent}
            newValue={newContent}
            splitView={splitView}
            useDarkTheme
            hideLineNumbers={false}
            disableWordDiff={false}
            compareMethod={DiffMethod.LINES}
            styles={DIFF_STYLES}
            hideSummary
            disableWorker
          />
        </div>
      )}
    </div>
  );
}

export function DiffViewer({ files }: { files: DiffFileChange[] }) {
  const [splitView, setSplitView] = useState(false);
  const [viewed, setViewed] = useState<Set<string>>(new Set());

  const toggleViewed = (path: string) => {
    setViewed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const total = useMemo(() => {
    let add = 0, del = 0;
    for (const f of files) {
      const s = countStats(f.patch);
      add += s.add; del += s.del;
    }
    return { add, del };
  }, [files]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-xs font-mono text-neutral-500">
          {files.length} file{files.length === 1 ? "" : "s"} changed{" "}
          <span className="text-emerald-400">+{total.add}</span>{" "}
          <span className="text-red-400">−{total.del}</span>
        </div>
        <div className="flex items-center gap-0 rounded-lg border border-neutral-800 bg-white/[0.02] p-0.5">
          <button
            onClick={() => setSplitView(false)}
            className={`px-2.5 py-1 text-[11px] font-mono rounded-md transition ${
              !splitView ? "bg-white/[0.08] text-neutral-100" : "text-neutral-500 hover:text-neutral-300"
            }`}
          >
            Unified
          </button>
          <button
            onClick={() => setSplitView(true)}
            className={`px-2.5 py-1 text-[11px] font-mono rounded-md transition ${
              splitView ? "bg-white/[0.08] text-neutral-100" : "text-neutral-500 hover:text-neutral-300"
            }`}
          >
            Split
          </button>
        </div>
      </div>

      <div className="space-y-2">
        {files.map((f) => (
          <DiffFileCard
            key={f.path}
            file={f}
            splitView={splitView}
            viewed={viewed.has(f.path)}
            onToggleViewed={() => toggleViewed(f.path)}
          />
        ))}
      </div>
    </div>
  );
}
