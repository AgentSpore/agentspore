import { expect, it, vi } from "vitest";
import Page from "./page";
import { redirect } from "next/navigation";

vi.mock("next/navigation", () => ({ redirect: vi.fn(() => { throw new Error("Synthetic redirect"); }) }));

it("redirects the retired hosted URL without rendering a hosting form", () => {
  expect(() => Page()).toThrow("Synthetic redirect");
  expect(redirect).toHaveBeenCalledWith("/workspace#connect-agent");
});
