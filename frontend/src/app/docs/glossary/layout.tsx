import type { Metadata } from "next";
import { getPageMetadata } from "@/lib/i18n/server";

export function generateMetadata(): Promise<Metadata> {
  return getPageMetadata("docs/glossary");
}

export default function DocsGlossaryLayout({ children }: { children: React.ReactNode }) {
  return children;
}
