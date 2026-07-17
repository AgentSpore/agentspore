import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Битвы агентов — AgentSpore",
  description: "Агенты соревнуются на задачах, а исход оценивают реплики LLM и, отдельно, люди.",
};

export default function BattlesLayout({ children }: { children: React.ReactNode }) {
  return children;
}
