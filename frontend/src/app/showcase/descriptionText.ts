import type { ShowcaseLanguage } from "./curatedServices";

const CYRILLIC = /[А-Яа-яЁё]/;

/**
 * Catalog descriptions are authored in one language. Returns the text only when its
 * script matches the page language, else null so the caller shows a fallback line.
 */
export function descriptionInLanguage(description: string | null | undefined, language: ShowcaseLanguage): string | null {
  const text = description?.trim();
  if (!text) return null;
  return CYRILLIC.test(text) === (language === "ru") ? text : null;
}

export function repoSlug(repoUrl: string | null): string | null {
  if (!repoUrl) return null;
  const parts = repoUrl.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || null;
}
