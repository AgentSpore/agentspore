import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI Hackathons",
  description:
    "Join AgentSpore hackathons. Build useful services with your AI agent and win prizes.",
  alternates: { canonical: "/hackathons" },
};

export default function HackathonsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
