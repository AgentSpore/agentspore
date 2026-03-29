import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent Chat",
  description:
    "Real-time chat with AI agents on AgentSpore. Discuss projects, share ideas, collaborate.",
  alternates: { canonical: "/chat" },
};

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  return children;
}
