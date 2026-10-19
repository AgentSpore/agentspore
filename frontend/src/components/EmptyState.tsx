import Link from "next/link";

interface EmptyStateProps {
  title: string;
  body: string;
  action: { href: string; label: string };
  learn: { href: string; label: string };
}

/** Teaching empty state: why the list is empty, one primary action, one link to the docs. */
export function EmptyState({ title, body, action, learn }: EmptyStateProps) {
  return (
    <div className="mx-auto max-w-md rounded-xl border border-neutral-800 bg-neutral-900/30 p-6 text-center">
      <h2 className="text-base font-semibold text-white">{title}</h2>
      <p className="mt-2 text-sm leading-6 text-neutral-300">{body}</p>
      <Link href={action.href} className="mt-4 inline-flex min-h-11 items-center justify-center rounded-lg bg-violet-600 px-4 text-sm font-medium text-white hover:bg-violet-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400">
        {action.label}
      </Link>
      <div className="mt-3">
        <Link href={learn.href} className="text-xs text-violet-300 underline underline-offset-2">{learn.label}</Link>
      </div>
    </div>
  );
}
