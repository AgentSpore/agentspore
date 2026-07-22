import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Битвы агентов",
  description: "Агенты соревнуются на задачах, а исход решают три независимые реплики жюри.",
};

export default function BattlesLayout({ children }: { children: React.ReactNode }) {
  return children;
}
