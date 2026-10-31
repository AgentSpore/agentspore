import { useCallback, useLayoutEffect, useRef, useState, type RefObject, type UIEvent } from "react";

/**
 * Tracks whether a scroll container has content below its visible edge.
 * Re-syncs on mount, on resize, on scroll and whenever `deps` change, because
 * a stable ref callback would only run once under the React Compiler.
 */
export function useMoreBelow(deps: readonly unknown[]): readonly [RefObject<HTMLDivElement | null>, boolean, (event: UIEvent<HTMLElement>) => void] {
  const ref = useRef<HTMLDivElement>(null);
  const [moreBelow, setMoreBelow] = useState(false);
  const sync = useCallback((el: HTMLElement | null) => {
    if (el) setMoreBelow(el.scrollTop + el.clientHeight < el.scrollHeight - 4);
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- deps are the caller's re-measure triggers
  useLayoutEffect(() => { sync(ref.current); }, [sync, ...deps]);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => sync(el));
    observer.observe(el);
    return () => observer.disconnect();
  }, [sync]);
  const onScroll = useCallback((event: UIEvent<HTMLElement>) => sync(event.currentTarget), [sync]);
  return [ref, moreBelow, onScroll];
}
