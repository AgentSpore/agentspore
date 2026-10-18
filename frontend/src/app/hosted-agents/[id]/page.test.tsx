import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import Page from "./page";
import { getLocale } from "@/lib/i18n/server";
import { WORKSPACE_MESSAGES } from "@/lib/i18n/agentWorkspace";

vi.mock("@/lib/i18n/server", () => ({ getLocale: vi.fn() }));
vi.mock("@/components/Header", () => ({ Header: () => null }));

it.each(["en", "ru"] as const)("explains retired hosting and links external connection in %s", async locale => {
  vi.mocked(getLocale).mockResolvedValue(locale);
  const html = renderToStaticMarkup(await Page());
  const copy = WORKSPACE_MESSAGES[locale];
  expect(html).toContain(copy.hostingRetired);
  expect(html).toContain(copy.hostingRetiredBody);
  expect(html).toContain('href="/workspace#connect-agent"');
  expect(html).not.toContain("<form");
});
