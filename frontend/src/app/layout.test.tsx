import { Children, isValidElement } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import RootLayout from "./layout";

const request = vi.hoisted(() => ({ locale: "en" }));
vi.mock("next/font/local", () => ({
  default: ({ variable }: { variable: string }) => ({ variable }),
}));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => ({ value: request.locale }) }),
  headers: async () => new Headers(),
}));

beforeEach(() => { request.locale = "en"; });

it.each([
  ["en", "Skip to content"],
  ["ru", "Перейти к содержимому"],
])("renders a native localized bypass before client providers in %s", async (locale, label) => {
  request.locale = locale;
  const html = await RootLayout({ children: <main id="main-content" tabIndex={-1}>Content</main> });
  const body = html.props.children;
  const elements = Children.toArray(body.props.children).filter(isValidElement);
  const first = elements[0];
  expect(first?.type).toBe("a");
  if (!isValidElement<{ href: string; children: string; onClick?: unknown }>(first)) {
    throw new Error("Expected the first body element to be the native skip link");
  }
  expect(first.props.href).toBe("#main-content");
  expect(first.props.children).toBe(label);
  expect(first.props.onClick).toBeUndefined();
  expect(html.props.lang).toBe(locale);
  expect(elements.some(element => typeof element.type !== "string")).toBe(true);
});

it("uses the local font variables", async () => {
  const html = await RootLayout({ children: <main /> });
  expect(html.props.children.props.className).toContain("--font-geist-sans");
  expect(html.props.children.props.className).toContain("--font-geist-mono");
});
