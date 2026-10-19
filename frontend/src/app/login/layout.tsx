import type { Metadata } from "next";
import { getPageMetadata } from "@/lib/i18n/server";

/** Metadata follows the request locale; the page itself is a client component. */
export function generateMetadata(): Promise<Metadata> {
  return getPageMetadata("login");
}

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  return children;
}
