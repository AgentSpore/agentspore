"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Props = {
  onTranscript: (text: string) => void;
  disabled?: boolean;
};

type RecState = "idle" | "listening" | "unsupported";

/**
 * Browser-native speech recognition button.
 * Uses the Web Speech API (SpeechRecognition) — works in Chrome, Edge, Safari.
 * No external API, no model download, runs on device.
 * Falls back to "unsupported" state in Firefox / older browsers.
 */
export function VoiceInput({ onTranscript, disabled }: Props) {
  const [state, setState] = useState<RecState>("idle");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const recognitionRef = useRef<any>(null);

  useEffect(() => {
    const SR = typeof window !== "undefined"
      ? ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)
      : null;
    if (!SR) {
      setState("unsupported");
      return;
    }
    const rec = new SR();
    rec.continuous = false;
    rec.interimResults = false;
    rec.lang = ""; // auto-detect
    rec.maxAlternatives = 1;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    rec.onresult = (e: any) => {
      const transcript = e.results[0]?.[0]?.transcript?.trim();
      if (transcript) onTranscript(transcript);
      setState("idle");
    };
    rec.onerror = () => setState("idle");
    rec.onend = () => setState("idle");

    recognitionRef.current = rec;
    return () => { rec.abort(); };
  }, [onTranscript]);

  const toggle = useCallback(() => {
    const rec = recognitionRef.current;
    if (!rec) return;
    if (state === "listening") {
      rec.stop();
      setState("idle");
    } else {
      rec.start();
      setState("listening");
    }
  }, [state]);

  if (state === "unsupported") return null;

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={disabled}
      title={state === "listening" ? "Stop recording" : "Voice input"}
      className={`shrink-0 w-10 h-10 rounded-lg flex items-center justify-center transition
        ${state === "listening"
          ? "bg-red-500/20 border border-red-500/40 text-red-400 animate-pulse"
          : "bg-neutral-900 border border-neutral-800 text-neutral-500 hover:text-white hover:border-neutral-700"
        } disabled:opacity-30`}
    >
      {state === "listening" ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="6" width="12" height="12" rx="2" />
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
          <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
          <line x1="12" y1="19" x2="12" y2="23" />
          <line x1="8" y1="23" x2="16" y2="23" />
        </svg>
      )}
    </button>
  );
}
