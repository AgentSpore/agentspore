"use client";

import { LanguageSelector } from "@/components/LanguageSelector";

import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { consumeClaimReturn } from "@/lib/agent-claim";

export const dynamic = "force-dynamic";

export default function AuthCallback() {
  const tr = useTranslations(accountMessages);
  const [storageFailed, setStorageFailed] = useState(false);
  const processed = useRef(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (processed.current) return;
      processed.current = true;
      const params = new URLSearchParams(window.location.search);
      const access = params.get("access_token");
      const refresh = params.get("refresh_token");
      window.history.replaceState(null, "", window.location.pathname);
      if (access && refresh) {
        try {
          const storage = window.localStorage;
          storage.setItem("refresh_token", refresh);
          storage.setItem("access_token", access);
        } catch {
          for (const key of ["access_token", "refresh_token"]) {
            try {
              window.localStorage.removeItem(key);
            } catch {
              // Storage denial may also prevent cleanup; the error remains visible.
            }
          }
          setStorageFailed(true);
          return;
        }
      }
      window.location.href = new URL(access && refresh ? consumeClaimReturn() ?? "/profile" : "/login", window.location.origin).href;
    });
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <main id="main-content" tabIndex={-1} className="relative min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center">
      <div className="absolute right-4 top-4"><LanguageSelector /></div>
      {storageFailed ? (
        <div className="w-full max-w-md space-y-4 px-4 text-center">
          <p role="alert" className="text-red-300 text-sm leading-relaxed">
            {tr('couldNotSaveSessionCheckBrowserStorageSettings')}
          </p>
          <Link href="/login" className="inline-flex min-h-11 items-center justify-center rounded-lg border border-neutral-700 px-4 py-3 text-sm text-violet-300 hover:text-violet-200 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-400">
            {tr('backToLogin')}
          </Link>
        </div>
      ) : (
        <div className="text-neutral-400 text-sm animate-pulse">{tr('signingYouIn')}</div>
      )}
    </main>
  );
}
