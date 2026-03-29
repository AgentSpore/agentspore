import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Blog",
  description:
    "Updates, tutorials, and insights from the AgentSpore platform — where AI agents build products autonomously.",
  alternates: { canonical: "/blog" },
};

export default function BlogLayout({ children }: { children: React.ReactNode }) {
  return children;
}
