// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { ProjectPrimaryAction } from "./ProjectPrimaryAction";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

afterEach(cleanup);

const LIVE = "https://service.example.test/app";
const REPO = "https://github.com/Example/demo";

function show(props: { deployUrl: string | null; repoUrl: string | null; archived?: boolean }, locale: "en" | "ru" = "en") {
  return render(<LocaleProvider initialLocale={locale}><ProjectPrimaryAction archived={false} {...props} /></LocaleProvider>);
}

describe("ProjectPrimaryAction", () => {
  it("offers the live service as the primary external action", () => {
    show({ deployUrl: LIVE, repoUrl: REPO });
    const primary = screen.getByRole("link", { name: "Open service" });
    expect(primary.getAttribute("href")).toBe(LIVE);
    expect(primary.getAttribute("rel")).toBe("noopener noreferrer");
    expect(primary.getAttribute("target")).toBe("_blank");
    expect(screen.getByRole("link", { name: "GitHub" }).getAttribute("href")).toBe(REPO);
    expect(screen.queryByRole("link", { name: "View source" })).toBeNull();
  });

  it("falls back to the repository when there is no live URL", () => {
    show({ deployUrl: null, repoUrl: REPO });
    expect(screen.getByRole("link", { name: "View source" }).getAttribute("href")).toBe(REPO);
    expect(screen.queryByRole("link", { name: "Open service" })).toBeNull();
    expect(screen.queryByRole("link", { name: "GitHub" })).toBeNull();
  });

  it("falls back to the repository for an archived project", () => {
    show({ deployUrl: LIVE, repoUrl: REPO, archived: true });
    expect(screen.getByRole("link", { name: "View source" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Open service" })).toBeNull();
  });

  it("renders nothing without any URL", () => {
    const { container } = show({ deployUrl: null, repoUrl: null });
    expect(container.firstChild).toBeNull();
  });

  it("speaks Russian", () => {
    show({ deployUrl: null, repoUrl: REPO }, "ru");
    expect(screen.getByRole("link", { name: "Смотреть исходный код" })).toBeTruthy();
    cleanup();
    show({ deployUrl: LIVE, repoUrl: REPO }, "ru");
    expect(screen.getByRole("link", { name: "Открыть сервис" })).toBeTruthy();
  });
});
