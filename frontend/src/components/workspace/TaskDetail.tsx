import { useId, useState } from "react";
import type { TaskDetail as Detail, TaskEvent, WorkspaceTask } from "@/lib/agent-workspace";
import { formatWorkspaceDate } from "@/lib/agent-workspace";
import { useLocale, useTranslations } from "@/lib/i18n/LocaleProvider";
import { WORKSPACE_MESSAGES } from "@/lib/i18n/agentWorkspace";

function DecisionHistory({ history }: { history: TaskEvent[] }) {
  const { locale } = useLocale(), t = useTranslations(WORKSPACE_MESSAGES);
  const [expanded, setExpanded] = useState(false);
  const historyId = useId();
  const visible = expanded ? history : history.slice(0, 5);
  return <section className="mt-6 border-t border-neutral-800 pt-5">
    <h3 className="text-lg font-semibold">{t("history")}</h3>
    {history.length === 0 ? <p className="mt-3 text-neutral-400">{t("noHistory")}</p> :
      <ol id={historyId} className="mt-4 divide-y divide-neutral-800">{visible.map((event, index) => {
        const label = event.action === "submit" || event.action === "accept" || event.action === "request_changes" || event.action === "reject" || event.action === "cancel" ? t(event.action) : `${t("unknownAction")}: ${event.action}`;
        return <li key={`${event.version}-${index}`} className="py-3 text-sm">
          <p className="font-medium text-neutral-200">{label}</p>
          <p className="mt-1 text-neutral-400">{event.actor.name} · {t("version")}: {event.version} · {t(event.state)}</p>
          <time dateTime={event.created_at} className="mt-1 block text-neutral-400">{formatWorkspaceDate(event.created_at, locale)}</time>
          {event.reason && <p className="mt-2 whitespace-pre-wrap wrap-anywhere text-neutral-300">{event.reason}</p>}
        </li>;
      })}</ol>}
    {history.length > 5 && <div className="mt-4"><p className="text-sm text-neutral-400">{t("historySummary", { shown: visible.length, total: history.length })}</p>
      <button aria-expanded={expanded} aria-controls={historyId} onClick={() => setExpanded(value => !value)} className="mt-3 inline-flex min-h-11 items-center rounded-lg border border-neutral-600 px-4 text-sm text-violet-300 hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300">{t(expanded ? "collapseHistory" : "expandHistory", { count: history.length })}</button>
    </div>}
  </section>;
}

function ReservationMetadata({ reservation }: { reservation: WorkspaceTask["learning_reservation"] }) {
  const t = useTranslations(WORKSPACE_MESSAGES);
  if (!reservation) return null;
  const label = reservation.status === "reserved" ? "reservedReceipt" : reservation.status === "refunded" ? "refundedReceipt" : "captured";
  return <><dt className="text-neutral-400">{t("units")}</dt><dd>{reservation.amount} · {t(label)}</dd></>;
}

function TaskMetadata({ detail }: { detail: Detail }) {
  const { locale } = useLocale(), t = useTranslations(WORKSPACE_MESSAGES);
  const status = detail.review_state ? t(detail.review_state) : taskStatusLabel(detail.task_status, t);
  return (
    <dl className="mt-5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-3 text-sm">
      <dt className="text-neutral-400">{t("status")}</dt><dd className="text-violet-300">{status}</dd>
      <dt className="text-neutral-400">{t("requester")}</dt><dd className="wrap-anywhere">{detail.requester?.name ?? t("unknown")}</dd>
      <dt className="text-neutral-400">{t("claimant")}</dt><dd className="wrap-anywhere">{detail.claimant?.name ?? t("unclaimed")}</dd>
      <dt className="text-neutral-400">{t("created")}</dt><dd>{detail.created_at ? <time dateTime={detail.created_at}>{formatWorkspaceDate(detail.created_at, locale)}</time> : t("unknown")}</dd>
      {detail.review_version !== null && <><dt className="text-neutral-400">{t("version")}</dt><dd>{detail.review_version}</dd></>}
      <ReservationMetadata reservation={detail.learning_reservation} />
    </dl>
  );
}

/** Display an authorized result as plain text and distinguish review from legacy completion. */
export function TaskDetail({ detail }: { detail: Detail }) {
  const t = useTranslations(WORKSPACE_MESSAGES);
  return <article className="min-w-0">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <h2 className="min-w-0 text-xl font-semibold wrap-anywhere">{detail.title}</h2>
      <span className="rounded-md bg-neutral-800 px-3 py-1 text-sm text-violet-300">{detail.relationship ? t(detail.relationship === "unknown" ? "relationshipUnknown" : detail.relationship) : t("relationshipUnknown")}</span>
    </div>
    <TaskMetadata detail={detail} />
    <p className="mt-4 text-sm leading-relaxed text-neutral-400">{detail.review_state ? t("relationshipInfo") : t("legacy")}</p>
    {detail.description && <p className="mt-4 whitespace-pre-wrap wrap-anywhere text-neutral-300">{detail.description}</p>}
    <section className="mt-6 border-t border-neutral-800 pt-5"><h3 className="text-lg font-semibold">{t("result")}</h3>
      <p className="mt-3 whitespace-pre-wrap wrap-anywhere leading-relaxed text-neutral-300">{detail.result ?? t("noResult")}</p>
    </section>
    <DecisionHistory key={detail.id} history={detail.history} />
  </article>;
}

const LEGACY_STATUS_KEYS = new Map<string, keyof typeof WORKSPACE_MESSAGES.en>([
  ["open", "taskOpen"], ["claimed", "taskClaimed"], ["in_progress", "taskInProgress"], ["completed", "taskCompleted"], ["failed", "taskFailed"], ["cancelled", "cancelled"],
]);

/** Translate known legacy states while keeping unexpected server states explicit. */
export function taskStatusLabel(status: string | null, t: ReturnType<typeof useTranslations<typeof WORKSPACE_MESSAGES.en>>): string {
  const key = status === null ? "unknown" : LEGACY_STATUS_KEYS.get(status);
  return key ? t(key) : `${t("taskUnknown")}: ${status}`;
}
