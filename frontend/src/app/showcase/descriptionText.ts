export function repoSlug(repoUrl: string | null): string | null {
  if (!repoUrl) return null;
  const parts = repoUrl.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || null;
}
