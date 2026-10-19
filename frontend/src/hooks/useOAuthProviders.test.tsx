// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useOAuthProviders } from "./useOAuthProviders";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(impl: () => Promise<unknown>) {
  const fetchMock = vi.fn<typeof fetch>(async () => (await impl()) as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

it("reports the configured providers from the public endpoint", async () => {
  const fetchMock = stubFetch(async () => ({ ok: true, json: async () => ({ github: true, google: false }) }));
  const { result } = renderHook(() => useOAuthProviders());
  expect(result.current).toEqual({ github: false, google: false });
  await waitFor(() => expect(result.current).toEqual({ github: true, google: false }));
  expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/v1/oauth/providers");
});

it.each([
  ["a network failure", async () => { throw new TypeError("offline"); }],
  ["an HTTP error", async () => ({ ok: false, json: async () => ({ github: true, google: true }) })],
  ["a malformed body", async () => ({ ok: true, json: async () => ({ github: "yes", google: 1 }) })],
])("shows no provider after %s", async (_name, impl) => {
  const fetchMock = stubFetch(impl);
  const { result } = renderHook(() => useOAuthProviders());
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(result.current).toEqual({ github: false, google: false });
});
