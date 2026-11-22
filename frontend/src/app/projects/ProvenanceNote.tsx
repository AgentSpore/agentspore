"use client";

import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { publicMessages } from "@/lib/i18n/public";
import type { Project } from "@/lib/api";

/** Footnote under the grid: imported GitHub history and missing authors. */
export function ProvenanceNote({ projects }: { projects: Project[] }) {
  const tr = useTranslations(publicMessages);
  const imported = projects.some(project => project.recovery_provenance);
  const recoveredNoAuthor = projects.some(project => !project.creator_agent_id && project.recovery_provenance);
  const otherNoAuthor = projects.some(project => !project.creator_agent_id && !project.recovery_provenance);
  if (!imported && !recoveredNoAuthor && !otherNoAuthor) return null;
  return <p data-testid="projects-footnote" className="mt-8 text-xs text-neutral-400">{imported && <>{tr('partialHistory')}{" "}</>}{recoveredNoAuthor && <>{tr('unknownAuthorNote')}{" "}</>}{otherNoAuthor && tr('someAuthorsMissing')}</p>;
}
