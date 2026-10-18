import { useCallback, useEffect, useRef, useState } from "react";

export type CopyState = "idle" | "copied" | "failed";

/** Copies text to the clipboard; never throws (missing API or denied permission yields "failed"). */
export function useCopyToClipboard(resetMs = 2000) {
  const [state, setState] = useState<CopyState>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const copy = useCallback(async (text: string): Promise<void> => {
    let next: CopyState = "copied";
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(text);
    } catch {
      next = "failed";
    }
    if (!mounted.current) return;
    setState(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), resetMs);
  }, [resetMs]);

  return { state, copy };
}
