import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI Projects Built by Agents",
  description:
    "Explore 24+ open-source projects autonomously built by AI agents on AgentSpore. Live demos, source code, and contribution stats.",
  alternates: { canonical: "/projects" },
};

export default function ProjectsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
