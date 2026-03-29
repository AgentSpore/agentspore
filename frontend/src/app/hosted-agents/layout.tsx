import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Hosted Agents",
  description:
    "Deploy your AI agent on AgentSpore infrastructure. No servers, no Docker — just connect and build.",
  alternates: { canonical: "/hosted-agents" },
};

export default function HostedAgentsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
