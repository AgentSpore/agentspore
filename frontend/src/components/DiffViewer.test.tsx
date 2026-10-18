// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DiffViewer } from "./DiffViewer";

vi.mock("react-diff-viewer-continued", () => ({ default: () => null, DiffMethod: { LINES: "lines" } }));

const files = [{ path: "a.ts", status: "modified", patch: "@@ -1 +1 @@\n-a\n+b" }];

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("DiffViewer copy button", () => {
  it("copies the patch and announces Copied", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<DiffViewer files={files} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Copied"));
    expect(writeText).toHaveBeenCalledWith(files[0]!.patch);
  });

  it("shows Copy failed without an unhandled rejection when the write is denied", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => { throw new Error("denied"); }) } });
    render(<DiffViewer files={files} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Copy failed"));
  });

  it("shows Copy failed when the clipboard API is missing", async () => {
    vi.stubGlobal("navigator", {});
    render(<DiffViewer files={files} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Copy failed"));
  });
});
