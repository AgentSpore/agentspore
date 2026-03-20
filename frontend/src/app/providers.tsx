"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WagmiProvider } from "wagmi";
import { wagmiConfig } from "@/lib/wagmi";
import ErrorBoundary from "@/components/ErrorBoundary";
import CommandPalette from "@/components/CommandPalette";
import { ToastProvider } from "@/components/Toast";
import ScrollToTop from "@/components/ScrollToTop";

const queryClient = new QueryClient();

export function Providers({ children }: { children: React.ReactNode }) {
  return (
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
  );
}
