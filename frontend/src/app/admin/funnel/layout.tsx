import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Funnel",
  robots: { index: false, follow: false },
};

export default function FunnelLayout({ children }: { children: React.ReactNode }) {
  return children;
}
