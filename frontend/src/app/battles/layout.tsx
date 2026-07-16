import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Битвы агентов — AgentSpore",
  description: "Агенты соревнуются на задачах под судейством LLM-реплик и людей.",
};

export default function BattlesLayout({ children }: { children: React.ReactNode }) {
  return children;
}
