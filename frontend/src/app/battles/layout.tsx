import type { Metadata } from "next";
import { getPageMetadata } from "@/lib/i18n/server";

/** Metadata follows the non-sensitive UI preference cookie. */
export function generateMetadata(): Promise<Metadata> {
  return getPageMetadata("battles");
}

export default function BattlesLayout({ children }: { children: React.ReactNode }) {
  return children;
}
