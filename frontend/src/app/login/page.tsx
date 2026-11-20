"use client";

import { WORKSPACE_MESSAGES } from "@/lib/i18n/agentWorkspace";

import { Header } from "@/components/Header";

import { useTranslations } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';
import { AUTH_FLOW_MESSAGES } from '@/lib/i18n/authFlow';
import { RewardBrief } from '@/components/welcome/RewardBrief';
import { ConfirmationSent } from './VerificationNotice';
import { AuthForm } from './AuthForm';

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

const ALLOWED_NEXT_PREFIXES = ["/dashboard", "/profile", "/agents", "/projects", "/chat", "/workspace", "/confirm-email-change"];
function safeNext(raw: string | null): string {
  if (!raw) return "/profile";
  if (!raw.startsWith("/")) return "/profile";
  if (raw.startsWith("//")) return "/profile";
  if (!ALLOWED_NEXT_PREFIXES.some(p => raw === p || raw.startsWith(p + "/") || raw.startsWith(p + "?"))) return "/profile";
  return raw;
}

function DotGrid() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{
        backgroundImage: "radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }} />
      <div className="absolute top-20 -left-32 w-[500px] h-[500px] rounded-full opacity-[0.07]"
        style={{ background: "radial-gradient(circle, rgb(139 92 246), transparent 70%)" }} />
      <div className="absolute bottom-20 right-0 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<main id="main-content" tabIndex={-1} aria-busy="true" />}>
      <LoginPageInner />
    </Suspense>
  );
}

function LoginPageInner() {
  const tr = useTranslations(accountMessages);
  const workspaceTr = useTranslations(WORKSPACE_MESSAGES);
  const flowTr = useTranslations(AUTH_FLOW_MESSAGES);
  const searchParams = useSearchParams();
  const nextUrl = safeNext(searchParams.get("next"));
  const [tab, setTab] = useState<"login" | "register">(() => searchParams.get("tab") === "register" ? "register" : "login");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [returnedEmail, setReturnedEmail] = useState<string | null>(null);

  const backToSignIn = () => {
    setReturnedEmail(sentTo);
    setSentTo(null);
    setTab("login");
  };

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      {/* Logo */}
      <Header />
      <main id="main-content" tabIndex={-1} className="relative flex justify-center px-4 py-10 pb-40 sm:py-16 sm:pb-40">
      <DotGrid />

      <div className="relative w-full max-w-md z-10">
        <div className="mb-8 space-y-3">
          <h1 className="text-3xl font-semibold tracking-tight">{tab === 'login' ? tr('signIn') : tr('createAccount')}</h1>
          <p className="text-sm leading-6 text-neutral-300">{tab === 'login' ? tr('accountAccessBenefit') : flowTr('registerSubtitle')}</p>
          <p className="text-sm leading-6 text-neutral-400">{tr('servicesWithoutAccount')}</p>
          <p className="text-xs leading-5 text-neutral-400">{workspaceTr('homeIntro')}</p>
        </div>

        {tab === 'register' && !sentTo && <div className="mb-6"><RewardBrief headingId="register-reward" /></div>}

        <div className="bg-neutral-900/30 border border-neutral-800/50 rounded-xl backdrop-blur-sm p-5 sm:p-8">
          {sentTo ? (
            <ConfirmationSent email={sentTo} onBack={backToSignIn} />
          ) : (
            <AuthForm tab={tab} onTabChange={setTab} nextUrl={nextUrl} defaultEmail={returnedEmail ?? ""}
              focusEmail={returnedEmail !== null} onRegistered={setSentTo} />
          )}
        </div>

      </div>

      </main>
    </div>
  );
}
