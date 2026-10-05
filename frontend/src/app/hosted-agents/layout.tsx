import type { Metadata } from "next";
import { getPageMetadata } from "@/lib/i18n/server";

/** Metadata follows the non-sensitive UI preference cookie. */
export function generateMetadata(): Promise<Metadata> {
  return getPageMetadata("hosted-agents");
}

export default function HostedAgentsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
