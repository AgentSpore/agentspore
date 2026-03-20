"use client";

import { useEffect, useState } from "react";

export default function ScrollToTop() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onScroll = () => {
      setVisible(window.scrollY > 400);
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const scrollToTop = () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <>
      <style>{`
        @keyframes stt-fade-in {
          from { opacity: 0; transform: translateY(12px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes stt-fade-out {
          from { opacity: 1; transform: translateY(0); }
          to   { opacity: 0; transform: translateY(12px); }
        }
        .stt-visible {
          animation: stt-fade-in 0.2s ease forwards;
          pointer-events: auto;
        }
        .stt-hidden {
          animation: stt-fade-out 0.2s ease forwards;
          pointer-events: none;
        }
      `}</style>

      <button
        onClick={scrollToTop}
        aria-label="Scroll to top"
        className={[
          "fixed bottom-6 right-6 z-50",
          "flex items-center justify-center",
          "w-10 h-10 rounded-full",
          "bg-neutral-900/80 border border-neutral-800 backdrop-blur-sm",
          "text-violet-400",
          "transition-all duration-200 ease-in-out",
          "hover:bg-neutral-800 hover:border-violet-500/30 hover:scale-105",
          visible ? "stt-visible" : "stt-hidden",
        ].join(" ")}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <polyline points="18 15 12 9 6 15" />
        </svg>
      </button>
    </>
  );
}
