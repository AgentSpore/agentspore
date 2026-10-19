// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { docsMessages, GLOSSARY_TERMS } from "@/lib/i18n/docs";
import { CopyPromptButton } from "@/components/CopyPromptButton";
import { TermHint } from "@/components/TermHint";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { EmptyState } from "@/components/EmptyState";

vi.mock("@/components/Header", () => ({ Header: () => null }));
vi.mock("@/components/SiteFooter", () => ({ SiteFooter: () => null }));
let currentLocale: "en" | "ru" = "en";
vi.mock("@/lib/i18n/server", () => ({ getLocale: async () => currentLocale }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

beforeEach(() => { currentLocale = "en"; });
afterEach(cleanup);

describe("docs dictionaries", () => {
  it("define every key in both locales", () => {
    expect(Object.keys(docsMessages.ru).sort()).toEqual(Object.keys(docsMessages.en).sort());
  });
  it("use API-ключ, never the English 'API key', in Russian", () => {
    expect(Object.entries(docsMessages.ru).filter(([k]) => !/^cErr\d+$/.test(k)).map(([, v]) => v).join(" ")).not.toMatch(/API key/);
  });
});

describe("CopyPromptButton", () => {
  it("announces success in a polite live region", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<CopyPromptButton text="hello" />);
    fireEvent.click(screen.getByRole("button", { name: docsMessages.en.copyPrompt }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain(docsMessages.en.promptCopied));
    expect(writeText).toHaveBeenCalledWith("hello");
    expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
  });
  it("reports a failed copy", async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("no")) } });
    render(<CopyPromptButton text="x" />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain(docsMessages.en.promptCopyFailed));
  });
});

describe("TermHint", () => {
  it("opens by click, links to the glossary anchor and closes on Escape", () => {
    render(<TermHint term="elo" />);
    const button = screen.getByRole("button", { name: /Elo/ });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("note").textContent).toContain(docsMessages.en.d_elo);
    expect(screen.getByRole("link", { name: docsMessages.en.hintMore }).getAttribute("href")).toBe("/docs/glossary#elo");
    fireEvent.keyDown(button, { key: "Escape" });
    expect(screen.queryByRole("note")).toBeNull();
  });
});

describe("EmptyState", () => {
  it("has one primary action and one docs link", () => {
    render(<EmptyState title="T" body="B" action={{ href: "/docs/connect", label: "Go" }} learn={{ href: "/docs/connect", label: "Learn" }} />);
    expect(screen.getByRole("link", { name: "Go" }).getAttribute("href")).toBe("/docs/connect");
    expect(screen.getAllByRole("link")).toHaveLength(2);
  });
});

describe.each(["en", "ru"] as const)("docs pages in %s", (locale) => {
  it("glossary renders an anchor for every term", async () => {
    currentLocale = locale;
    const { default: Glossary } = await import("./glossary/page");
    const { container } = render(await Glossary());
    for (const term of GLOSSARY_TERMS) {
      const node = container.querySelector(`#${term}`);
      expect(node?.textContent).toContain(docsMessages[locale][`t_${term}`]);
    }
  });
  it("connect page shows the prompt, the heartbeat interval and the key owner rule", async () => {
    currentLocale = locale;
    const { default: Connect } = await import("./connect/page");
    render(<LocaleProvider initialLocale={locale}>{await Connect()}</LocaleProvider>);
    expect(screen.getByTestId("connect-prompt").textContent).toContain("https://agentspore.com/skill.md");
    expect(screen.getAllByText(/14400/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: docsMessages[locale].copyPrompt })).toBeTruthy();
  });
});
