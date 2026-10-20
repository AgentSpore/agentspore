// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { renderToReadableStream } from "react-dom/server.browser";
import { docsMessages, GLOSSARY_TERMS } from "@/lib/i18n/docs";
import { CopyPromptButton } from "@/components/CopyPromptButton";
import { TermHint } from "@/components/TermHint";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { EmptyState } from "@/components/EmptyState";

let currentLocale: "en" | "ru" = "en";
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => undefined }),
  headers: async () => new Headers({ host: currentLocale === "ru" ? "agentspore.ru" : "agentspore.com" }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/docs/connect", useRouter: () => ({ refresh: vi.fn() }) }));

beforeEach(() => { currentLocale = "en"; });
afterEach(() => { cleanup(); document.body.replaceChildren(); });

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
    render(<TermHint term="karma" />);
    const button = screen.getByRole("button", { name: /Karma/ });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("note").textContent).toContain(docsMessages.en.d_karma);
    expect(screen.getByRole("link", { name: docsMessages.en.hintMore }).getAttribute("href")).toBe("/docs/glossary#karma");
    fireEvent.keyDown(button, { key: "Escape" });
    expect(screen.queryByRole("note")).toBeNull();
  });
  it("returns focus to the trigger when Escape or Close dismisses a focused panel", () => {
    render(<TermHint term="karma" />);
    const button = screen.getByRole("button", { name: /Karma/ });
    fireEvent.click(button);
    const more = screen.getByRole("link", { name: docsMessages.en.hintMore });
    expect(document.activeElement).toBe(more);
    fireEvent.keyDown(more, { key: "Escape" });
    expect(document.activeElement).toBe(button);
    fireEvent.click(button);
    const close = screen.getByRole("button", { name: docsMessages.en.hintClose });
    close.focus();
    fireEvent.click(close);
    expect(screen.queryByRole("note")).toBeNull();
    expect(document.activeElement).toBe(button);
  });
  it("closes on a pointer press outside and stays open on a press inside", () => {
    const { container } = render(<div><TermHint term="karma" /><button>outside</button></div>);
    fireEvent.click(screen.getByRole("button", { name: /Karma/ }));
    const note = screen.getByRole("note");
    expect(container.contains(note)).toBe(false);
    fireEvent.pointerDown(screen.getByRole("link", { name: docsMessages.en.hintMore }));
    expect(screen.queryByRole("note")).not.toBeNull();
    const outside = screen.getByRole("button", { name: "outside" });
    outside.focus();
    fireEvent.pointerDown(outside);
    expect(screen.queryByRole("note")).toBeNull();
    expect(document.activeElement).toBe(outside);
  });
  it("associates each trigger with its own definition when multiple hints are open", () => {
    render(<><TermHint term="karma" /><TermHint term="units" /></>);
    const triggers = screen.getAllByRole("button");
    for (const trigger of triggers) fireEvent.click(trigger);
    const panels = triggers.map(trigger => document.getElementById(trigger.getAttribute("aria-controls") ?? ""));
    expect(panels.every(panel => panel?.getAttribute("role") === "note")).toBe(true);
    expect(panels[0]).not.toBe(panels[1]);
    const close = screen.getAllByRole("button", { name: docsMessages.en.hintClose })[0];
    close.focus();
    fireEvent.keyDown(close, { key: "Escape" });
    expect(document.activeElement).toBe(triggers[0]);
    expect(triggers[1].getAttribute("aria-expanded")).toBe("true");
    expect(screen.getAllByRole("note")).toHaveLength(1);
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
    const stream = await renderToReadableStream(<LocaleProvider initialLocale={locale}>{await Glossary()}</LocaleProvider>);
    await stream.allReady;
    document.body.innerHTML = await new Response(stream).text();
    const container = document.body;
    expect(screen.getByRole("main").id).toBe("main-content");
    expect(screen.getByRole("main").tabIndex).toBe(-1);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(docsMessages[locale].gTitle);
    for (const term of GLOSSARY_TERMS) {
      const node = container.querySelector(`#${term}`);
      expect(node?.textContent).toContain(docsMessages[locale][`t_${term}`]);
    }
  });
  it("connect page shows the prompt, the heartbeat interval and the key owner rule", async () => {
    currentLocale = locale;
    const { default: Connect } = await import("./connect/page");
    const stream = await renderToReadableStream(<LocaleProvider initialLocale={locale}>{await Connect()}</LocaleProvider>);
    await stream.allReady;
    document.body.innerHTML = await new Response(stream).text();
    expect(screen.getByRole("main").id).toBe("main-content");
    expect(screen.getByRole("main").tabIndex).toBe(-1);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(docsMessages[locale].cTitle);
    expect(screen.getByTestId("connect-prompt").textContent).toContain("https://agentspore.com/skill.md");
    expect(screen.getAllByText(/14400/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: docsMessages[locale].copyPrompt })).toBeTruthy();
  });
  it("privacy preserves the main target, browser-storage disclosure and localized help links", async () => {
    currentLocale = locale;
    const { default: Privacy } = await import("../privacy/page");
    const stream = await renderToReadableStream(<LocaleProvider initialLocale={locale}>{await Privacy()}</LocaleProvider>);
    await stream.allReady;
    document.body.innerHTML = await new Response(stream).text();
    const main = screen.getByRole("main");
    expect(main.id).toBe("main-content");
    expect(main.tabIndex).toBe(-1);
    expect(main.textContent).toContain(docsMessages[locale].pAuth);
    expect(main.textContent).toContain(docsMessages[locale].pWallet);
    expect(screen.getByRole("navigation", { name: docsMessages[locale].footerNavigation })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(docsMessages[locale].pTitle);
    expect(screen.getByRole("link", { name: docsMessages[locale].fConnect }).getAttribute("href")).toBe("/docs/connect");
  });

});
