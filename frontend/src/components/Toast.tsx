"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

// ─── Types ────────────────────────────────────────────────────────────────────

export type ToastType = "success" | "error" | "info" | "warning";

interface ToastItem {
  id: string;
  message: string;
  type: ToastType;
  duration: number;
  /** Timestamp when the toast was created — used to drive the progress bar. */
  createdAt: number;
  /** When true the exit animation is playing. */
  exiting: boolean;
}

interface ToastContextValue {
  toast: (message: string, type?: ToastType, duration?: number) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
  warning: (message: string) => void;
}

// ─── Context ──────────────────────────────────────────────────────────────────

const ToastContext = createContext<ToastContextValue | null>(null);

// ─── Config ───────────────────────────────────────────────────────────────────

const MAX_TOASTS = 5;
const DEFAULT_DURATION = 4000;
const EXIT_DURATION = 320; // must match CSS animation duration

// ─── Per-type visuals ─────────────────────────────────────────────────────────

const TYPE_CONFIG: Record<
  ToastType,
  { border: string; label: string; bar: string; icon: ReactNode }
> = {
  success: {
    border: "border-emerald-500/30",
    label: "text-emerald-400",
    bar: "bg-emerald-500",
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="2,7 5.5,10.5 12,3.5" />
      </svg>
    ),
  },
  error: {
    border: "border-red-500/30",
    label: "text-red-400",
    bar: "bg-red-500",
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <line x1="3" y1="3" x2="11" y2="11" />
        <line x1="11" y1="3" x2="3" y2="11" />
      </svg>
    ),
  },
  info: {
    border: "border-cyan-500/30",
    label: "text-cyan-400",
    bar: "bg-cyan-500",
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <circle cx="7" cy="7" r="5.5" />
        <line x1="7" y1="6" x2="7" y2="10" />
        <circle cx="7" cy="4" r="0.5" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
  warning: {
    border: "border-amber-500/30",
    label: "text-amber-400",
    bar: "bg-amber-500",
    icon: (
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M7 1.5L12.5 11.5H1.5L7 1.5Z" />
        <line x1="7" y1="5.5" x2="7" y2="8.5" />
        <circle cx="7" cy="10" r="0.5" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
};

// ─── Single toast card ────────────────────────────────────────────────────────

function ToastCard({
  item,
  onDismiss,
}: {
  item: ToastItem;
  onDismiss: (id: string) => void;
}) {
  const cfg = TYPE_CONFIG[item.type];

  /** Tick state just to force re-render for the progress bar width calculation. */
  const [, setTick] = useState(0);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    // Drive progress bar repaints at ~60 fps for smooth shrink.
    const tick = () => {
      setTick((t) => t + 1);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, []);

  const elapsed = Date.now() - item.createdAt;
  const progress = Math.max(0, 1 - elapsed / item.duration);

  return (
    <div
      className={`toast-card relative flex flex-col overflow-hidden rounded-xl border ${cfg.border} bg-[#0a0a0a] shadow-[0_8px_32px_rgba(0,0,0,0.7)] w-80 ${item.exiting ? "toast-exit" : "toast-enter"}`}
    >
      {/* Body */}
      <div className="flex items-start gap-3 px-4 py-3.5">
        {/* Icon */}
        <div className={`mt-px flex-shrink-0 ${cfg.label}`}>
          {cfg.icon}
        </div>

        {/* Text */}
        <div className="flex-1 min-w-0">
          <p className={`text-[11px] font-mono uppercase tracking-widest mb-0.5 ${cfg.label}`}>
            {item.type}
          </p>
          <p className="text-[13px] text-neutral-200 leading-snug break-words">
            {item.message}
          </p>
        </div>

        {/* Dismiss button */}
        <button
          onClick={() => onDismiss(item.id)}
          className="flex-shrink-0 mt-0.5 text-neutral-600 hover:text-neutral-300 transition-colors"
          aria-label="Dismiss"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round">
            <line x1="2" y1="2" x2="10" y2="10" />
            <line x1="10" y1="2" x2="2" y2="10" />
          </svg>
        </button>
      </div>

      {/* Progress bar */}
      <div className="h-[2px] bg-neutral-900 mx-0">
        <div
          className={`h-full ${cfg.bar} opacity-60 transition-none`}
          style={{ width: `${progress * 100}%` }}
        />
      </div>
    </div>
  );
}

// ─── Provider ─────────────────────────────────────────────────────────────────

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const scheduleRemoval = useCallback((id: string, delay: number) => {
    const t = setTimeout(() => {
      // Trigger exit animation first.
      setToasts((prev) =>
        prev.map((t) => (t.id === id ? { ...t, exiting: true } : t))
      );
      // Remove from DOM after animation completes.
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
        timersRef.current.delete(id);
      }, EXIT_DURATION);
    }, delay);
    timersRef.current.set(id, t);
  }, []);

  const dismiss = useCallback(
    (id: string) => {
      const existing = timersRef.current.get(id);
      if (existing) { clearTimeout(existing); timersRef.current.delete(id); }
      setToasts((prev) =>
        prev.map((t) => (t.id === id ? { ...t, exiting: true } : t))
      );
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, EXIT_DURATION);
    },
    []
  );

  const addToast = useCallback(
    (message: string, type: ToastType = "info", duration: number = DEFAULT_DURATION) => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const item: ToastItem = { id, message, type, duration, createdAt: Date.now(), exiting: false };

      setToasts((prev) => {
        const next = [...prev, item];
        // If over limit, begin exit animation on the oldest non-exiting toast.
        if (next.filter((t) => !t.exiting).length > MAX_TOASTS) {
          const oldest = next.find((t) => !t.exiting);
          if (oldest) {
            // Schedule removal via dismiss path (clears its auto-dismiss timer too).
            setTimeout(() => dismiss(oldest.id), 0);
          }
        }
        return next;
      });

      scheduleRemoval(id, duration);
    },
    [scheduleRemoval, dismiss]
  );

  // Cleanup all timers on unmount.
  useEffect(() => {
    const timers = timersRef.current;
    return () => { timers.forEach(clearTimeout); timers.clear(); };
  }, []);

  const ctx: ToastContextValue = {
    toast: addToast,
    success: (msg) => addToast(msg, "success"),
    error: (msg) => addToast(msg, "error"),
    info: (msg) => addToast(msg, "info"),
    warning: (msg) => addToast(msg, "warning"),
  };

  return (
    <ToastContext.Provider value={ctx}>
      {children}

      <style jsx global>{`
        @keyframes toast-slide-in {
          from {
            opacity: 0;
            transform: translateX(calc(100% + 16px));
          }
          to {
            opacity: 1;
            transform: translateX(0);
          }
        }
        @keyframes toast-slide-out {
          from {
            opacity: 1;
            transform: translateX(0);
            max-height: 120px;
            margin-bottom: 0px;
          }
          to {
            opacity: 0;
            transform: translateX(calc(100% + 16px));
            max-height: 0px;
            margin-bottom: -8px;
          }
        }
        .toast-enter {
          animation: toast-slide-in 0.3s cubic-bezier(0.16, 1, 0.3, 1) both;
        }
        .toast-exit {
          animation: toast-slide-out ${EXIT_DURATION}ms cubic-bezier(0.4, 0, 1, 1) both;
          pointer-events: none;
        }
      `}</style>

      {/* Toast stack — fixed bottom-right */}
      {toasts.length > 0 && (
        <div
          className="fixed bottom-5 right-5 z-[9999] flex flex-col gap-2 items-end"
          aria-live="polite"
          aria-label="Notifications"
        >
          {toasts.map((item) => (
            <ToastCard key={item.id} item={item} onDismiss={dismiss} />
          ))}
        </div>
      )}
    </ToastContext.Provider>
  );
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within a <ToastProvider>.");
  }
  return ctx;
}
