import type { Metadata } from "next";
import { getPageMetadata } from "@/lib/i18n/server";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  return getPageMetadata("agentProfile", `/agents/${encodeURIComponent(id)}`);
}

export default function AgentProfileLayout({ children }: { children: React.ReactNode }) {
  return children;
}
