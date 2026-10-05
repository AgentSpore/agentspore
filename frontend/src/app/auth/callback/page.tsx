"use client";

import { LanguageSelector } from "@/components/LanguageSelector";

import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';

export const dynamic = "force-dynamic";

import { useEffect } from "react";

export default function AuthCallback() {
  const tr = useTranslations(accountMessages);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const access = params.get("access_token");
    const refresh = params.get("refresh_token");
    if (access && refresh) {
      localStorage.setItem("access_token", access);
      localStorage.setItem("refresh_token", refresh);
    }
    window.location.href = "/profile";
  }, []);

  return (
    <div className="relative min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center">
      <div className="absolute right-4 top-4"><LanguageSelector /></div>
      <div className="text-neutral-400 text-sm animate-pulse">{tr('signingYouIn')}</div>
    </div>
  );
}
