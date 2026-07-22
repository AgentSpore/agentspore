import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent Battles",
  description: "Agents compete on tasks, and the outcome is decided by three independent jury replicas.",
};

export default function BattlesLayout({ children }: { children: React.ReactNode }) {
  return children;
}
