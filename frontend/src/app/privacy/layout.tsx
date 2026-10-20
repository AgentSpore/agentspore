import type { Metadata } from "next";
import { getPageMetadata } from "@/lib/i18n/server";

export function generateMetadata(): Promise<Metadata> {
  return getPageMetadata("privacy");
}

export default function PrivacyLayout({ children }: { children: React.ReactNode }) {
  return children;
}
