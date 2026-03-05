"use client";

import { useEffect } from "react";
import { useSearchParams } from "next/navigation";

export default function AuthCallback() {
  const params = useSearchParams();

  useEffect(() => {
    const access = params.get("access_token");
    const refresh = params.get("refresh_token");
    if (access && refresh) {
      localStorage.setItem("access_token", access);
      localStorage.setItem("refresh_token", refresh);
    }
    window.location.href = "/";
  }, [params]);

  return (
    <div className="min-h-screen bg-[#080b12] text-white flex items-center justify-center">
      <div className="text-slate-400 text-sm animate-pulse">Signing you in...</div>
    </div>
  );
}
