import { describe, expect, it } from "vitest";
import { blogExcerpt, formatIsoDates } from "./excerpt";

describe("formatIsoDates", () => {
  it("replaces ISO timestamps with a locale-formatted date", () => {
    const text = "Range 2026-10-27T17:00:42.221665+00:00 to 2026-10-28T09:00:00Z done";
    expect(formatIsoDates(text, "en")).toBe("Range Oct 27, 2026 to Oct 28, 2026 done");
    expect(formatIsoDates(text, "ru")).toBe("Range 27 окт. 2026 г. to 28 окт. 2026 г. done");
  });

  it("leaves text without timestamps and impossible dates alone", () => {
    expect(formatIsoDates("Plain 2026-10-27 note", "en")).toBe("Plain 2026-10-27 note");
    expect(formatIsoDates("Bad 2026-13-45T99:00:00Z", "en")).toBe("Bad 2026-13-45T99:00:00Z");
  });
});

describe("blogExcerpt", () => {
  it("strips markdown marks, formats dates and clamps at a word boundary", () => {
    const out = blogExcerpt("# Title **bold** `x` 2026-10-27T17:00:42+00:00 " + "word ".repeat(80), "en", 60);
    expect(out).toContain("Oct 27, 2026");
    expect(out).not.toMatch(/[#*`]/);
    expect(out.endsWith("…")).toBe(true);
    expect(Array.from(out).length).toBeLessThanOrEqual(61);
  });

  it("returns short text unchanged and does not split a surrogate pair", () => {
    expect(blogExcerpt("short text", "en")).toBe("short text");
    expect(blogExcerpt("🪴".repeat(300), "en", 10)).toBe("🪴".repeat(10) + "…");
  });
});
