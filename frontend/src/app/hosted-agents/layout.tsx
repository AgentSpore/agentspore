import type { Metadata } from "next";

export const metadata: Metadata = { robots: { index: false, follow: false } };

/** Keep legacy URLs available without advertising permanent hosting. */
export default function HostedAgentsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
