import { describe, expect, it } from "vitest";

const modules = import.meta.glob(
  ["./*.ts", "!./*.test.ts", "!./locale.ts", "!./server.ts"],
  { eager: true },
) as Record<string, Record<string, unknown>>;

function texts(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (typeof value !== "object" || value === null) return [];
  return Object.values(value).flatMap(texts);
}

describe("i18n copy", () => {
  it("does not promise token-weighted voting", () => {
    const promise = /token-weighted|голосовани[ея][^.]*токен|токен[^.]*голосовани|voting is planned|token[^.]*\bvot|\bvot[^.]*token/i;
    const offenders = Object.values(modules).flatMap(texts).filter(text => promise.test(text));
    expect(offenders).toEqual([]);
  });
});
