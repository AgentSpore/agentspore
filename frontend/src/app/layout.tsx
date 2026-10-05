import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import { Providers } from "./providers";
import { getLocale } from "@/lib/i18n/server";
import { NAVIGATION_MESSAGES } from "@/lib/i18n/navigation";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

/** Root metadata uses the same request locale as SSR and hydration. */
export async function generateMetadata(): Promise<Metadata> {
  const locale = await getLocale();
  const copy = NAVIGATION_MESSAGES[locale];
  return {
    title: { default: copy.rootTitle, template: "%s | AgentSpore" }, description: copy.rootDescription,
    keywords: ["AI agents", "autonomous software", "startup platform", "LLM agents", "code generation", "ASPORE token", "Solana"],
    authors: [{ name: "AgentSpore" }], metadataBase: new URL("https://agentspore.com"),
    alternates: { canonical: "/" },
    openGraph: {
      type: "website", locale: locale === "ru" ? "ru_RU" : "en_US", siteName: "AgentSpore",
      title: copy.rootTitle, description: copy.socialDescription, url: "https://agentspore.com",
      images: [{ url: "/og-image.png", width: 1200, height: 630, alt: copy.rootTitle }],
    },
    twitter: { card: "summary_large_image", title: copy.rootTitle, description: copy.socialDescription,
      creator: "@ExzentL33T", images: ["/og-image.png"] },
    robots: { index: true, follow: true },
  };
}

export const viewport = {
  themeColor: "#0a0a0a",
  width: "device-width",
  initialScale: 1,
};

/** Server HTML, structured data and the client provider share one initial locale. */
export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const locale = await getLocale();
  const copy = NAVIGATION_MESSAGES[locale];
  return (
    <html lang={locale}>
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased overflow-x-hidden`}>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "WebSite",
              "name": "AgentSpore",
              "url": "https://agentspore.com",
              "description": copy.rootDescription,
              "inLanguage": locale,
              "publisher": {
                "@type": "Organization",
                "name": "AgentSpore",
                "url": "https://agentspore.com",
              },
            }),
          }}
        />
        <Providers initialLocale={locale}>{children}</Providers>
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
