import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { publicMessages } from "@/lib/i18n/public";

const PRIMARY = "inline-flex min-h-11 min-w-11 max-w-full items-center justify-center whitespace-nowrap px-5 py-2 rounded-xl text-sm font-medium font-mono bg-white text-black transition-all hover:bg-neutral-200 focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300";
const SECONDARY = "inline-flex min-h-11 min-w-11 max-w-full items-center justify-center text-xs text-neutral-400 hover:text-white bg-neutral-800/30 border border-neutral-800/50 hover:border-neutral-700/60 px-3.5 py-1.5 rounded-lg font-mono transition-all focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300";

interface Props { deployUrl: string | null; repoUrl: string | null; archived: boolean }

export function ProjectPrimaryAction({ deployUrl, repoUrl, archived }: Props) {
  const tr = useTranslations(publicMessages);
  const live = archived ? null : deployUrl;
  const primaryUrl = live ?? repoUrl;
  if (!primaryUrl) return null;
  return (
    <div className="mt-5 flex flex-wrap items-center gap-3">
      <a href={primaryUrl} target="_blank" rel="noopener noreferrer" className={PRIMARY}>
        {live ? tr("openService") : tr("viewSource")}
      </a>
      {live && repoUrl && (
        <a href={repoUrl} target="_blank" rel="noopener noreferrer" className={SECONDARY}>GitHub</a>
      )}
    </div>
  );
}
