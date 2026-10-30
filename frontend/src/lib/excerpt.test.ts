import { describe, expect, it } from "vitest";
import { formatIsoDates } from "./excerpt";

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

  it("keeps the author's calendar date when the timestamp carries an offset", () => {
    expect(formatIsoDates("At 2026-10-27T23:30:00-05:00 and 2026-10-28T00:30:00+03:00", "en")).toBe("At Oct 27, 2026 and Oct 28, 2026");
  });
});
