import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

export interface OAuthProviders { github: boolean; google: boolean }

const NONE: OAuthProviders = { github: false, google: false };

/**
 * Human-login providers the backend has credentials for. Unknown (loading or
 * failed) is reported as "none" so a dead sign-in button is never shown.
 */
export function useOAuthProviders(): OAuthProviders {
  const [providers, setProviders] = useState<OAuthProviders>(NONE);
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const res = await fetch(`${API_URL}/api/v1/oauth/providers`, { signal: controller.signal });
        const body: unknown = res?.ok ? await res.json() : null;
        if (typeof body !== "object" || body === null) return;
        const flags = body as Record<string, unknown>;
        setProviders({ github: flags.github === true, google: flags.google === true });
      } catch {
        // Unknown stays "none": better no OAuth button than one that answers 501.
      }
    })();
    return () => controller.abort();
  }, []);
  return providers;
}
