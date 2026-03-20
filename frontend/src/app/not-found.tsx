"use client";

import Link from "next/link";
import { Header } from "@/components/Header";

function DotGrid() {
  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: "radial-gradient(circle, #ffffff 1px, transparent 1px)",
          backgroundSize: "32px 32px",
        }}
      />
      <div className="absolute top-[-20%] left-[-10%] w-[60vw] h-[60vw] rounded-full bg-violet-500 opacity-[0.025] blur-[120px]" />
      <div className="absolute bottom-[-30%] right-[-15%] w-[50vw] h-[50vw] rounded-full bg-cyan-500 opacity-[0.02] blur-[100px]" />
    </div>
  );
}

export default function NotFound() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white overflow-x-hidden">
      <style jsx global>{`
        @keyframes glitch-1 {
          0%, 90%, 100% { clip-path: inset(0 0 100% 0); transform: translate(0); }
          91% { clip-path: inset(8% 0 58% 0); transform: translate(-4px, 0); }
          93% { clip-path: inset(40% 0 30% 0); transform: translate(4px, 0); }
          95% { clip-path: inset(70% 0 5% 0); transform: translate(-2px, 0); }
          97% { clip-path: inset(20% 0 70% 0); transform: translate(3px, 0); }
          99% { clip-path: inset(55% 0 15% 0); transform: translate(-3px, 0); }
        }
        @keyframes glitch-2 {
          0%, 85%, 100% { clip-path: inset(0 0 100% 0); transform: translate(0); }
          86% { clip-path: inset(25% 0 50% 0); transform: translate(4px, 0); }
          88% { clip-path: inset(60% 0 15% 0); transform: translate(-4px, 0); }
          90% { clip-path: inset(10% 0 75% 0); transform: translate(2px, 0); }
          92% { clip-path: inset(80% 0 2% 0); transform: translate(-2px, 0); }
        }
        @keyframes scanline-404 {
          0% { top: -4px; opacity: 0; }
          5% { opacity: 1; }
          95% { opacity: 0.6; }
          100% { top: 100%; opacity: 0; }
        }
        @keyframes flicker {
          0%, 97%, 100% { opacity: 1; }
          98% { opacity: 0.85; }
          99% { opacity: 0.92; }
        }
        @keyframes fadeInUp {
          from { opacity: 0; transform: translateY(20px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        .fade-in    { animation: fadeInUp 0.7s ease-out both; }
        .fade-in-d1 { animation: fadeInUp 0.7s ease-out 0.12s both; }
        .fade-in-d2 { animation: fadeInUp 0.7s ease-out 0.24s both; }
        .fade-in-d3 { animation: fadeInUp 0.7s ease-out 0.36s both; }
        .fade-in-d4 { animation: fadeInUp 0.7s ease-out 0.48s both; }
        .num-404 {
          animation: flicker 6s ease-in-out infinite;
        }
        .glitch-layer-1 {
          animation: glitch-1 7s ease-in-out infinite;
          color: #a78bfa;
        }
        .glitch-layer-2 {
          animation: glitch-2 7s ease-in-out 0.3s infinite;
          color: #22d3ee;
        }
        .scanline-bar {
          animation: scanline-404 3.5s linear infinite;
        }
      `}</style>

      <DotGrid />
      <Header />

      <main className="relative z-10 flex flex-col items-center justify-center min-h-[calc(100vh-80px)] px-6 py-20 text-center">

        {/* ── 404 number block ── */}
        <div className="fade-in relative select-none mb-8">

          {/* Base 404 */}
          <div
            className="num-404 relative text-[clamp(7rem,22vw,16rem)] font-black tracking-[-0.06em] leading-none bg-gradient-to-b from-violet-400 via-indigo-400 to-cyan-400 bg-clip-text text-transparent"
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            404

            {/* Glitch layers — absolutely positioned over the base text */}
            <span
              aria-hidden="true"
              className="glitch-layer-1 absolute inset-0 bg-gradient-to-b from-violet-400 via-indigo-400 to-cyan-400 bg-clip-text text-transparent"
            >
              404
            </span>
            <span
              aria-hidden="true"
              className="glitch-layer-2 absolute inset-0 bg-gradient-to-b from-cyan-400 via-indigo-400 to-violet-400 bg-clip-text text-transparent"
            >
              404
            </span>

            {/* Scan-line passing over the 404 */}
            <span
              aria-hidden="true"
              className="scanline-bar pointer-events-none absolute left-0 right-0 h-[3px] bg-gradient-to-r from-transparent via-violet-400/50 to-transparent"
              style={{ top: 0 }}
            />
          </div>

          {/* Subtle border glow beneath the number */}
          <div className="absolute -inset-x-6 bottom-0 h-px bg-gradient-to-r from-transparent via-violet-500/30 to-transparent" />
        </div>

        {/* ── Terminal error line ── */}
        <div className="fade-in-d1 font-mono text-[13px] text-neutral-500 tracking-[0.12em] mb-4">
          <span className="text-violet-400/70">$</span>
          {" "}
          <span className="text-red-400/80">Error:</span>
          {" "}
          <span className="text-neutral-400">page_not_found</span>
          <span className="inline-block w-2 h-4 bg-neutral-500/60 ml-1 align-middle animate-pulse" />
        </div>

        {/* ── Subtitle ── */}
        <p className="fade-in-d2 text-neutral-500 text-[15px] leading-relaxed max-w-sm mb-10 font-light">
          The page you&apos;re looking for doesn&apos;t exist or has been moved.
        </p>

        {/* ── Action buttons ── */}
        <div className="fade-in-d3 flex items-center justify-center gap-3 flex-wrap mb-14">
          <Link
            href="/"
            className="px-7 py-3 rounded-xl text-sm font-medium font-mono bg-white text-black transition-all hover:bg-neutral-200 hover:scale-[1.02]"
          >
            Go Home
          </Link>
          <Link
            href="/dashboard"
            className="px-7 py-3 rounded-xl text-sm font-medium font-mono text-violet-300 bg-violet-500/10 border border-violet-500/20 hover:bg-violet-500/20 hover:border-violet-500/30 transition-all"
          >
            Dashboard
          </Link>
        </div>

        {/* ── Terminal card with quick links ── */}
        <div className="fade-in-d4 w-full max-w-sm bg-neutral-900/60 border border-neutral-800/80 rounded-2xl overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-neutral-800/60">
            <div className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
            <div className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" />
            <div className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
            <span className="text-[10px] text-neutral-600 font-mono ml-2">platform://navigation</span>
          </div>
          <div className="p-4 space-y-1 font-mono text-[12px]">
            {[
              { label: "~/agents", href: "/agents", accent: "text-cyan-400" },
              { label: "~/projects", href: "/projects", accent: "text-emerald-400" },
              { label: "~/hackathons", href: "/hackathons", accent: "text-orange-400" },
              { label: "~/chat", href: "/chat", accent: "text-violet-400" },
            ].map(({ label, href, accent }) => (
              <Link
                key={href}
                href={href}
                className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-neutral-500 hover:text-neutral-200 hover:bg-white/[0.04] transition-all group"
              >
                <span className={`${accent} opacity-60 group-hover:opacity-100 transition-opacity`}>›</span>
                {label}
                <span className="ml-auto text-neutral-700 group-hover:text-neutral-500 transition-colors">↵</span>
              </Link>
            ))}
          </div>
        </div>

      </main>
    </div>
  );
}
