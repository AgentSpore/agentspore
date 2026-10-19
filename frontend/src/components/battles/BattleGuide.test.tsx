// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnonChallengeNotice, BattleFaq, useIsAnonymous } from "./BattleGuide";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
beforeEach(() => localStorage.clear());
afterEach(cleanup);

function Gate() {
  return useIsAnonymous() ? <AnonChallengeNotice /> : <p>signed-in</p>;
}

describe.each(["en", "ru"] as const)("battle guide in %s", (locale) => {
  it("explains why sign-in is needed and links to login for an anonymous visitor", () => {
    render(<LocaleProvider initialLocale={locale}><Gate /></LocaleProvider>);
    expect(screen.getByText(docsMessages[locale].bAnonBody)).toBeTruthy();
    expect(screen.getByRole("link", { name: docsMessages[locale].bSignIn }).getAttribute("href")).toBe("/login?next=/battles/new");
  });
  it("shows no sign-in notice to a signed-in user", () => {
    localStorage.setItem("access_token", "synthetic");
    render(<LocaleProvider initialLocale={locale}><Gate /></LocaleProvider>);
    expect(screen.getByText("signed-in")).toBeTruthy();
    expect(screen.queryByText(docsMessages[locale].bAnonBody)).toBeNull();
  });
  it("renders the FAQ with a glossary link", () => {
    render(<LocaleProvider initialLocale={locale}><BattleFaq /></LocaleProvider>);
    expect(screen.getByText(docsMessages[locale].bQ1)).toBeTruthy();
    expect(screen.getByRole("link", { name: docsMessages[locale].openGlossary }).getAttribute("href")).toBe("/docs/glossary#rated");
  });
});
