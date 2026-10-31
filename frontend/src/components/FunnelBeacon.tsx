"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { buildBeacon, sendVisit, trackingOptOut } from "@/lib/funnel/beacon";

/** Sends one anonymous visit beacon per client-side page view; renders nothing. */
export function FunnelBeacon() {
  const pathname = usePathname();
  const lastPath = useRef<string | null>(null);

  useEffect(() => {
    if (!pathname || lastPath.current === pathname) return;
    const entry = lastPath.current === null;
    lastPath.current = pathname;
    const nav = navigator as Navigator & { globalPrivacyControl?: boolean };
    if (trackingOptOut(nav)) return;
    const beacon = buildBeacon(pathname, entry, window.location.search, document.referrer, window.location.hostname);
    if (beacon) sendVisit(beacon);
  }, [pathname]);

  return null;
}
