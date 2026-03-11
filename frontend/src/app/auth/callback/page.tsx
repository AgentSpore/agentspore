"use client";

export const dynamic = "force-dynamic";

import { useEffect } from "react";

export default function AuthCallback() {
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
    <div className="min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center">
      <div className="text-neutral-400 text-sm animate-pulse">Signing you in...</div>
    </div>
  );
}
