import { API_URL } from "@/lib/api";

/** Exactly the fields `POST /api/v1/funnel/visit` accepts (backend VisitBeacon). */
export interface VisitBeacon {
  path: string;
  utm_source?: string;
  referrer?: string;
}

const MAX_PATH = 200;
const MAX_UTM = 64;

interface BrowserSignals {
  doNotTrack?: string | null;
  globalPrivacyControl?: boolean;
}

/** True when the visitor opted out through Do Not Track or Global Privacy Control. */
export function trackingOptOut(nav: BrowserSignals): boolean {
  return nav.doNotTrack === "1" || nav.globalPrivacyControl === true;
}

/** Private areas (admin, signed-in pages, token flows) are never reported, even to be discarded. */
const PRIVATE_PREFIXES = [
  "/admin", "/dashboard", "/profile", "/workspace", "/auth",
  "/reset-password", "/forgot-password", "/verify-email",
];

/** Public pages within the schema limit; private areas and over-long paths are skipped. */
export function isCountablePath(path: string): boolean {
  if (path.length === 0 || path.length > MAX_PATH) return false;
  return !PRIVATE_PREFIXES.some(prefix => path === prefix || path.startsWith(`${prefix}/`));
}

function externalOrigin(referrer: string, ownHost: string): string | undefined {
  try {
    const url = new URL(referrer);
    if (!/^https?:$/.test(url.protocol) || url.hostname === ownHost) return undefined;
    return url.origin;
  } catch {
    return undefined;
  }
}

/**
 * Body for one visit. The query string never leaves the browser except
 * `utm_source`, and the referrer is cut to its origin. Both are attached only
 * on the entry page of a load: later in-app navigations carry no new origin.
 */
export function buildBeacon(path: string, entry: boolean, search: string, referrer: string, ownHost: string): VisitBeacon | null {
  if (!isCountablePath(path)) return null;
  const beacon: VisitBeacon = { path };
  if (!entry) return beacon;
  const utm = new URLSearchParams(search).get("utm_source")?.trim();
  if (utm && utm.length <= MAX_UTM) beacon.utm_source = utm;
  const origin = externalOrigin(referrer, ownHost);
  if (origin) beacon.referrer = origin;
  return beacon;
}

/** Fire-and-forget: never throws, nothing to await, no cookies or credentials. */
export function sendVisit(beacon: VisitBeacon): void {
  try {
    void fetch(`${API_URL}/api/v1/funnel/visit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(beacon),
      keepalive: true,
      credentials: "omit",
    }).catch(() => undefined);
  } catch {
    // The beacon must never affect the page.
  }
}
