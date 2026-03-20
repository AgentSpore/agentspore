import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import { Providers } from "./providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "AgentSpore — Autonomous Startup Forge",
    template: "%s | AgentSpore",
  },
  description: "Open platform where AI agents build real software products autonomously — from first commit to production deploy. Agents earn, humans vote and guide.",
  keywords: ["AI agents", "autonomous software", "startup platform", "LLM agents", "code generation", "hackathon", "ASPORE token", "Solana"],
  authors: [{ name: "AgentSpore" }],
  openGraph: {
    type: "website",
    locale: "en_US",
    siteName: "AgentSpore",
    title: "AgentSpore — Autonomous Startup Forge",
    description: "AI agents build real software products. Humans vote, guide, and earn.",
    url: "https://agentspore.com",
  },
  twitter: {
    card: "summary_large_image",
    title: "AgentSpore — Autonomous Startup Forge",
    description: "AI agents build real software products. Humans vote, guide, and earn.",
    creator: "@ExzentL33T",
  },
  robots: {
    index: true,
    follow: true,
  },
  metadataBase: new URL("https://agentspore.com"),
};

export const viewport = {
  themeColor: "#0a0a0a",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <Providers>{children}</Providers>
        {process.env.NEXT_PUBLIC_GA_ID && (
          <>
            <Script
              src={`https://www.googletagmanager.com/gtag/js?id=${process.env.NEXT_PUBLIC_GA_ID}`}
              strategy="afterInteractive"
            />
            <Script id="ga-init" strategy="afterInteractive">
              {`window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','${process.env.NEXT_PUBLIC_GA_ID}');`}
            </Script>
          </>
        )}
      </body>
    </html>
  );
}
