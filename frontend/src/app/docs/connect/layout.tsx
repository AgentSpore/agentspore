import type { Metadata } from "next";
import { getPageMetadata } from "@/lib/i18n/server";

export function generateMetadata(): Promise<Metadata> {
  return getPageMetadata("docs/connect");
}

export default function DocsConnectLayout({ children }: { children: React.ReactNode }) {
  return children;
}
