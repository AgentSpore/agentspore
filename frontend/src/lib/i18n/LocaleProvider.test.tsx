// @vitest-environment jsdom
import { cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LocaleProvider, useLocale, useTranslations } from "./LocaleProvider";
import { LOCALE_COOKIE, type Locale } from "./locale";
import { useState } from "react";

const refresh = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
afterEach(() => {
  cleanup(); vi.restoreAllMocks(); refresh.mockReset();
  document.cookie = `${LOCALE_COOKIE}=; Path=/; Max-Age=0`;
});

const messages = { en: { name: "Your name", result: "Hello {name}, {count} tasks" }, ru: { name: "Ваше имя", result: "Здравствуйте, {name}, задач: {count}" } };
function FormProbe({ page = "one" }: { page?: string }) {
  const { locale, setLocale } = useLocale();
  const t = useTranslations(messages);
  const [name, setName] = useState("example");
  return <><label>{t("name")}<input value={name} onChange={event => setName(event.target.value)} /></label>
    <p>{t("result", { name, count: 0 })}</p><span>{page}: {locale}</span>
    <button type="button" onClick={() => setLocale(locale === "en" ? "ru" : "en")}>Switch</button></>;
}

describe("global locale provider", () => {
  it("keeps translator identity stable on unchanged renders", () => {
    const { result, rerender } = renderHook(() => useTranslations(messages));
    const translator = result.current;
    rerender();
    expect(result.current).toBe(translator);
    expect(result.current("result", { name: "example", count: 0 })).toBe("Hello example, 0 tasks");
  });
  it("uses English for isolated callers", () => {
    render(<FormProbe />);
    expect(screen.getByLabelText("Your name")).toBeTruthy();
  });
  it("switches, preserves draft on refresh/navigation, persists for reload and updates html language", () => {
    const view = render(<LocaleProvider initialLocale="en"><FormProbe /></LocaleProvider>);
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Draft example" } });
    fireEvent.click(screen.getByRole("button", { name: "Switch" }));
    expect(screen.getByLabelText("Ваше имя")).toHaveProperty("value", "Draft example");
    expect(document.documentElement.lang).toBe("ru");
    expect(document.cookie).toContain(`${LOCALE_COOKIE}=ru`);
    expect(refresh).toHaveBeenCalledOnce();
    view.rerender(<LocaleProvider initialLocale="ru"><FormProbe page="two" /></LocaleProvider>);
    expect(screen.getByLabelText("Ваше имя")).toHaveProperty("value", "Draft example");
    expect(screen.getByText("two: ru")).toBeTruthy();
    view.unmount();
    render(<LocaleProvider initialLocale="ru"><FormProbe /></LocaleProvider>);
    expect(screen.getByLabelText("Ваше имя")).toBeTruthy();
  });
  it("does not block a UI switch when cookies are denied", () => {
    render(<LocaleProvider initialLocale="en"><FormProbe /></LocaleProvider>);
    vi.spyOn(document, "cookie", "set").mockImplementation(() => { throw new Error("Storage denied"); });
    fireEvent.click(screen.getByRole("button", { name: "Switch" }));
    expect(screen.getByLabelText("Ваше имя")).toBeTruthy();
    expect(refresh).not.toHaveBeenCalled();
  });
  it("keeps UI usable without refreshing server metadata when cookie writes silently fail", () => {
    render(<LocaleProvider initialLocale="en"><FormProbe /></LocaleProvider>);
    vi.spyOn(document, "cookie", "set").mockImplementation(() => {});
    fireEvent.click(screen.getByRole("button", { name: "Switch" }));
    expect(screen.getByLabelText("Ваше имя")).toBeTruthy();
    expect(document.documentElement.lang).toBe("ru");
    expect(refresh).not.toHaveBeenCalled();
  });
  it.each(["en", "ru"] as const)("renders the initial %s locale before hydration", (initialLocale: Locale) => {
    const html = renderToString(<LocaleProvider initialLocale={initialLocale}><FormProbe /></LocaleProvider>);
    expect(html).toContain(initialLocale === "ru" ? "Ваше имя" : "Your name");
  });
  it("inserts placeholder values as text rather than HTML", () => {
    render(<LocaleProvider initialLocale="en"><FormProbe /></LocaleProvider>);
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "<script>example</script>" } });
    expect(screen.getByText("Hello <script>example</script>, 0 tasks")).toBeTruthy();
    expect(document.querySelector("script")).toBeNull();
  });
});
