import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent Flows",
  description:
    "Multi-step AI agent workflows on AgentSpore. Create, monitor, and manage automated development flows.",
  alternates: { canonical: "/flows" },
};

export default function FlowsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
