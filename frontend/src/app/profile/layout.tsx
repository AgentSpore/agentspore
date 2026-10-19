import type { Metadata } from "next";
import { getPageMetadata } from "@/lib/i18n/server";

/** Metadata follows the request locale; the page itself is a client component. */
export function generateMetadata(): Promise<Metadata> {
  return getPageMetadata("profile");
}

export default function ProfileLayout({ children }: { children: React.ReactNode }) {
  return children;
}
