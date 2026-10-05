"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WagmiProvider } from "wagmi";
import { wagmiConfig } from "@/lib/wagmi";
import ErrorBoundary from "@/components/ErrorBoundary";
import CommandPalette from "@/components/CommandPalette";
import { ToastProvider } from "@/components/Toast";
import ScrollToTop from "@/components/ScrollToTop";

import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import type { Locale } from "@/lib/i18n/locale";

const queryClient = new QueryClient();

/** Shared client providers retain their state when the UI language changes. */
export function Providers({ children, initialLocale = "en" }: { children: React.ReactNode; initialLocale?: Locale }) {
  return (
    <LocaleProvider initialLocale={initialLocale}>
    <ErrorBoundary>
      <WagmiProvider config={wagmiConfig}>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            {children}
            <CommandPalette />
            <ScrollToTop />
          </ToastProvider>
        </QueryClientProvider>
      </WagmiProvider>
    </ErrorBoundary>
    </LocaleProvider>
  );
}
