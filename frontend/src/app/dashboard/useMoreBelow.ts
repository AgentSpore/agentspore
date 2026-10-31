import { useCallback, useEffect, useLayoutEffect, useState, type UIEvent } from "react";

const measure = (el: HTMLElement) => el.scrollTop + el.clientHeight < el.scrollHeight - 4;

/**
 * Tracks whether a scroll container has content below its visible edge.
 * The returned callback ref follows the element's mount, so the resize observer
 * attaches even when the container appears after the first render.
 * Re-syncs on mount, on resize, on scroll and whenever `deps` change.
 */
export function useMoreBelow(deps: readonly unknown[]): readonly [(el: HTMLDivElement | null) => void, boolean, (event: UIEvent<HTMLElement>) => void] {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const [moreBelow, setMoreBelow] = useState(false);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- deps are the caller's re-measure triggers
  useLayoutEffect(() => { if (el) setMoreBelow(measure(el)); }, [el, ...deps]);
  useEffect(() => {
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setMoreBelow(measure(el)));
    observer.observe(el);
    return () => observer.disconnect();
  }, [el]);
  const onScroll = useCallback((event: UIEvent<HTMLElement>) => setMoreBelow(measure(event.currentTarget)), []);
  return [setEl, moreBelow, onScroll];
}
