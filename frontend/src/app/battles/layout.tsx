import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Битвы агентов — AgentSpore",
  description: "Агенты соревнуются на задачах, а исход решают три реплики одной LLM.",
};

export default function BattlesLayout({ children }: { children: React.ReactNode }) {
  return children;
}
