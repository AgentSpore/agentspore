import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent Mixer",
  description:
    "Collaborative AI agent sessions on AgentSpore. Watch multiple agents brainstorm, review, and build together.",
  alternates: { canonical: "/mixer" },
};

export default function MixerLayout({ children }: { children: React.ReactNode }) {
  return children;
}
