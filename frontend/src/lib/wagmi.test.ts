// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const originalStorage = Object.getOwnPropertyDescriptor(window, "localStorage");

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Unexpected synthetic RPC request"); }));
});
afterEach(() => {
  if (originalStorage) Object.defineProperty(window, "localStorage", originalStorage);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("creates the real wallet config when the browser storage getter is denied", async () => {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    get: () => { throw new DOMException("Synthetic storage denied", "SecurityError"); },
  });
  const { wagmiConfig } = await import("./wagmi");
  expect(wagmiConfig.chains.map(chain => chain.id)).toEqual([8453]);
  expect(wagmiConfig.connectors.map(connector => connector.id)).toContain("injected");
  expect(wagmiConfig._internal.ssr).toBe(true);
  expect(await wagmiConfig.storage?.getItem("recentConnectorId")).toBeNull();
  await expect(wagmiConfig.storage?.setItem("recentConnectorId", "synthetic-injected")).resolves.toBeUndefined();
  await expect(wagmiConfig.storage?.removeItem("recentConnectorId")).resolves.toBeUndefined();
  expect(fetch).not.toHaveBeenCalled();
  wagmiConfig._internal.mipd?.destroy();
});

it("fails closed when storage reads are denied after configuration", async () => {
  const { wagmiConfig } = await import("./wagmi");
  const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new DOMException("Synthetic read denied", "SecurityError");
  });
  expect(await wagmiConfig.storage?.getItem("recentConnectorId")).toBeNull();
  read.mockRestore();
  await wagmiConfig.storage?.setItem("recentConnectorId", "synthetic-recovered");
  expect(await wagmiConfig.storage?.getItem("recentConnectorId")).toBe("synthetic-recovered");
  expect(fetch).not.toHaveBeenCalled();
  wagmiConfig._internal.mipd?.destroy();
});

it.each(["setItem", "removeItem"] as const)("tolerates denied optional wallet persistence: %s", async method => {
  const { wagmiConfig } = await import("./wagmi");
  vi.spyOn(Storage.prototype, method).mockImplementation(() => {
    throw new DOMException("Synthetic write denied", "SecurityError");
  });
  await expect(wagmiConfig.storage?.[method]("recentConnectorId", "synthetic-injected")).resolves.toBeUndefined();
  expect(fetch).not.toHaveBeenCalled();
  wagmiConfig._internal.mipd?.destroy();
});

it("preserves normal wallet persistence and its existing key namespace", async () => {
  const { wagmiConfig } = await import("./wagmi");
  await wagmiConfig.storage?.setItem("recentConnectorId", "synthetic-injected");
  expect(localStorage.getItem("wagmi.recentConnectorId")).not.toBeNull();
  expect(await wagmiConfig.storage?.getItem("recentConnectorId")).toBe("synthetic-injected");
  await wagmiConfig.storage?.removeItem("recentConnectorId");
  expect(await wagmiConfig.storage?.getItem("recentConnectorId")).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
  wagmiConfig._internal.mipd?.destroy();
});
