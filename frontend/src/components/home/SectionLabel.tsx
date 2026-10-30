/** Small mono uppercase section label. */
export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-xs font-mono tracking-[0.25em] uppercase text-neutral-400">
      {children}
    </span>
  );
}
