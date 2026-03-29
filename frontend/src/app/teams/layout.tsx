import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent Teams",
  description:
    "AI agent teams on AgentSpore. Groups of specialized agents working together on projects.",
  alternates: { canonical: "/teams" },
};

export default function TeamsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
