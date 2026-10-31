// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { FunnelBeacon } from "./FunnelBeacon";

let pathname: string | null = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

const fetchMock = vi.fn();
beforeEach(() => {
  pathname = "/";
  fetchMock.mockReset().mockResolvedValue({});
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("fires once per navigation, not per re-render", () => {
  const { rerender } = render(<FunnelBeacon />);
  rerender(<FunnelBeacon />);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  pathname = "/agents";
  rerender(<FunnelBeacon />);
  rerender(<FunnelBeacon />);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(JSON.parse((fetchMock.mock.calls[1] as [string, RequestInit])[1].body as string)).toEqual({ path: "/agents" });
});

it("does not fire on admin pages", () => {
  pathname = "/admin/funnel";
  render(<FunnelBeacon />);
  expect(fetchMock).not.toHaveBeenCalled();
});

it.each([
  ["doNotTrack", "1"],
  ["globalPrivacyControl", true],
])("sends nothing when %s is set", (key, value) => {
  Object.defineProperty(navigator, key, { value, configurable: true });
  render(<FunnelBeacon />);
  expect(fetchMock).not.toHaveBeenCalled();
  Reflect.deleteProperty(navigator, key);
});

it("a failed fetch does not break rendering", () => {
  fetchMock.mockRejectedValue(new Error("offline"));
  expect(() => render(<FunnelBeacon />)).not.toThrow();
});
