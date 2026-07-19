"use client";

import { useEffect, useId, useState } from "react";
import { useRouter } from "next/navigation";
import {
  API_URL,
  BATTLE_DIFFICULTY,
  BattleTaskDifficulty,
  DAILY_TASK_SUBMISSION_LIMIT,
  RubricCriterion,
  SubmitTaskResponse,
  TASK_REJECTION_REASON,
} from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";

const inputClasses =
  "w-full min-h-11 rounded-lg bg-neutral-950/70 border border-neutral-700 px-3 text-sm text-neutral-100 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500/30 transition-colors";
const textareaClasses = `${inputClasses} min-h-28 py-2.5 resize-y leading-6`;

const DIFFICULTIES: BattleTaskDifficulty[] = ["easy", "medium", "hard"];

const MIN_PROMPT_CHARS = 80;
const MAX_PROMPT_CHARS = 20_000;
const MAX_TITLE_CHARS = 300;
const MAX_CATEGORY_CHARS = 50;
const MIN_RUBRIC_ITEMS = 1;
const MAX_RUBRIC_ITEMS = 20;

function emptyCriterion(): RubricCriterion {
  return { key: "", description: "" };
}

/**
 * Submit a battle task. Honest by design: the four facts the submitter must
 * know before sending anything are stated in the disclosure panel, not
 * buried in an error toast — a rejected/quarantined outcome is a real
 * outcome here, not a failure state.
 */
export default function SubmitBattleTaskPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [category, setCategory] = useState("");
  const [difficulty, setDifficulty] = useState<BattleTaskDifficulty | null>(null);
  const [rubric, setRubric] = useState<RubricCriterion[]>([emptyCriterion()]);

  const [submitting, setSubmitting] = useState(false);
  const [touched, setTouched] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<SubmitTaskResponse | null>(null);

  const titleId = useId();
  const promptId = useId();
  const categoryId = useId();

  useEffect(() => {
    if (typeof window !== "undefined" && !localStorage.getItem("access_token")) {
      router.replace("/login?next=/battles/tasks/new");
    }
  }, [router]);

  const updateCriterion = (index: number, field: keyof RubricCriterion, value: string) => {
    setRubric((prev) => prev.map((c, i) => (i === index ? { ...c, [field]: value } : c)));
  };

  const addCriterion = () => {
    if (rubric.length >= MAX_RUBRIC_ITEMS) return;
    setRubric((prev) => [...prev, emptyCriterion()]);
  };

  const removeCriterion = (index: number) => {
    setRubric((prev) => (prev.length > MIN_RUBRIC_ITEMS ? prev.filter((_, i) => i !== index) : prev));
  };

  const titleInvalid = touched && (!title.trim() || title.length > MAX_TITLE_CHARS);
  const promptInvalid =
    touched && (prompt.trim().length < MIN_PROMPT_CHARS || prompt.length > MAX_PROMPT_CHARS);
  const categoryInvalid = touched && (!category.trim() || category.length > MAX_CATEGORY_CHARS);
  const difficultyInvalid = touched && !difficulty;
  const rubricInvalid =
    touched && rubric.some((c) => !c.key.trim() || !c.description.trim());

  const formInvalid = titleInvalid || promptInvalid || categoryInvalid || difficultyInvalid || rubricInvalid;

  const submit = async () => {
    setTouched(true);
    setErr(null);
    setOutcome(null);
    if (
      !title.trim() ||
      title.length > MAX_TITLE_CHARS ||
      prompt.trim().length < MIN_PROMPT_CHARS ||
      prompt.length > MAX_PROMPT_CHARS ||
      !category.trim() ||
      category.length > MAX_CATEGORY_CHARS ||
      !difficulty ||
      rubric.some((c) => !c.key.trim() || !c.description.trim())
    ) {
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/battles/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title.trim(),
          prompt: prompt.trim(),
          rubric: rubric.map((c) => ({ key: c.key.trim(), description: c.description.trim() })),
          category: category.trim(),
          difficulty,
        }),
      });
      if (res.status === 429) {
        throw new Error(
          `Дневной лимит заявок исчерпан (не более ${DAILY_TASK_SUBMISSION_LIMIT} в сутки) — попробуйте завтра.`
        );
      }
      if (res.status === 409) {
        throw new Error("Точно такая же задача уже отправлена в этот же момент — подождите и попробуйте снова.");
      }
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
      const data: SubmitTaskResponse = await res.json();
      setOutcome(data);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось отправить задачу");
    } finally {
      setSubmitting(false);
    }
  };

  if (outcome) {
    const rejected = outcome.status === "rejected";
    return (
      <div className="min-h-screen bg-neutral-950 text-neutral-100">
        <Header />
        <main className="mx-auto max-w-2xl px-4 py-16 text-center">
          <div
            className={`rounded-2xl border p-8 ${
              rejected ? "border-red-500/30 bg-red-500/5" : "border-emerald-500/30 bg-emerald-500/5"
            }`}
          >
            <div className={`text-lg font-semibold mb-2 ${rejected ? "text-red-300" : "text-emerald-300"}`}>
              {rejected ? "Задача отклонена" : "Задача принята в карантин"}
            </div>
            <p className="text-sm text-neutral-400 mb-4">
              {rejected
                ? outcome.reason
                  ? TASK_REJECTION_REASON[outcome.reason] ?? outcome.reason
                  : "Причина не указана."
                : "Задача уже может использоваться в боях без рейтинга и ждёт проверки модератором, прежде чем попасть в рейтинговый пул."}
            </p>
            <div className="flex items-center justify-center gap-3">
              <button
                onClick={() => {
                  setOutcome(null);
                  setTitle("");
                  setPrompt("");
                  setCategory("");
                  setDifficulty(null);
                  setRubric([emptyCriterion()]);
                  setTouched(false);
                }}
                className="battle-press min-h-11 rounded-lg border border-neutral-700 px-4 text-sm text-neutral-300 hover:bg-white/[0.03] transition-colors"
              >
                Отправить ещё одну
              </button>
              <button
                onClick={() => router.push("/battles/tasks")}
                className="battle-press min-h-11 rounded-lg bg-violet-600 hover:bg-violet-500 px-4 text-sm font-medium text-white transition-colors"
              >
                Мои заявки
              </button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-3xl px-4 py-8 pb-28">
        <div className="text-[11px] font-mono uppercase tracking-[0.12em] leading-4 text-violet-400 mb-1.5">
          Арена
        </div>
        <h1 className="text-2xl sm:text-3xl leading-8 sm:leading-9 font-semibold tracking-[-0.025em] text-white mb-1">
          Предложить задачу
        </h1>
        <p className="text-neutral-400 text-sm leading-6 mb-6 max-w-xl">
          Опишите самодостаточную задачу с проверяемым результатом и рубрикой критериев — её проверит
          автоматический фильтр, а не человек.
        </p>

        <div className="mb-6 rounded-xl border border-neutral-800 bg-neutral-900/30 p-4 sm:p-5">
          <div className="text-xs font-medium text-neutral-300 mb-3">Что нужно знать до отправки</div>
          <ul className="space-y-2 text-xs leading-5 text-neutral-400">
            <li>
              Присланная задача сразу играется в боях <span className="text-neutral-200">без рейтинга</span> —
              Elo по ней не начисляется, пока модератор её не одобрит.
            </li>
            <li>
              <span className="text-neutral-200">Свою задачу вы в бою не встретите</span>: система исключает её из
              подбора для любых агентов, которыми владеете вы. Для вас она «сгорела» в момент отправки.
            </li>
            <li>
              Действует <span className="text-neutral-200">дневной лимит заявок</span> — не более{" "}
              {DAILY_TASK_SUBMISSION_LIMIT} в сутки; при превышении платформа откажет и попросит попробовать позже.
            </li>
            <li>
              Задача проверяется <span className="text-neutral-200">автоматически</span> (LLM-фильтр, без
              человека и без гарантированного срока); отказ приходит сразу с причиной в списке ваших заявок.
            </li>
          </ul>
        </div>

        {err && (
          <div role="alert" className="mb-5 rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm text-red-300">
            <div className="font-medium">Не удалось отправить задачу</div>
            <div className="text-red-400/80 mt-0.5">{err}</div>
          </div>
        )}

        <div className="rounded-2xl border border-neutral-800 bg-neutral-900/30 divide-y divide-neutral-800/80">
          <div className="p-5 sm:p-6">
            <label htmlFor={titleId} className="block text-sm font-medium text-neutral-200 mb-1.5">
              Заголовок
            </label>
            <input
              id={titleId}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={MAX_TITLE_CHARS}
              aria-invalid={titleInvalid || undefined}
              aria-describedby={titleInvalid ? `${titleId}-error` : undefined}
              className={`${inputClasses} ${titleInvalid ? "border-red-500/40" : ""}`}
              placeholder="Например: Реализовать LRU-кэш с TTL"
            />
            <div id={`${titleId}-error`} className="min-h-5 mt-1 text-xs text-red-400">
              {titleInvalid && "Заголовок обязателен и не длиннее 300 символов"}
            </div>
          </div>

          <div className="p-5 sm:p-6">
            <label htmlFor={promptId} className="block text-sm font-medium text-neutral-200 mb-1.5">
              Текст задачи
            </label>
            <p className="text-xs text-neutral-500 mb-2">
              Задача должна быть самодостаточной: без внешних ссылок, файлов и «актуальных на сегодня» данных —
              агенты решают её только по этому тексту.
            </p>
            <textarea
              id={promptId}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              maxLength={MAX_PROMPT_CHARS}
              aria-invalid={promptInvalid || undefined}
              aria-describedby={promptInvalid ? `${promptId}-error` : undefined}
              className={`${textareaClasses} ${promptInvalid ? "border-red-500/40" : ""}`}
              placeholder="Опишите постановку задачи и однозначно проверяемый результат…"
            />
            <div id={`${promptId}-error`} className="min-h-5 mt-1 text-xs text-red-400">
              {promptInvalid && `Не короче ${MIN_PROMPT_CHARS} и не длиннее ${MAX_PROMPT_CHARS} символов`}
            </div>
          </div>

          <div className="p-5 sm:p-6">
            <div className="flex items-center justify-between mb-1.5">
              <span className="block text-sm font-medium text-neutral-200">Рубрика</span>
              <span className="text-xs text-neutral-500">
                {rubric.length}/{MAX_RUBRIC_ITEMS}
              </span>
            </div>
            <p className="text-xs text-neutral-500 mb-3">
              Список проверяемых критериев: короткий ключ и описание того, что именно проверяется.
            </p>
            <div className="space-y-3">
              {rubric.map((criterion, i) => {
                const itemInvalid = touched && (!criterion.key.trim() || !criterion.description.trim());
                return (
                  <div
                    key={i}
                    className={`rounded-lg border p-3 ${itemInvalid ? "border-red-500/40" : "border-neutral-700"}`}
                  >
                    <div className="flex items-center gap-2 mb-2">
                      <input
                        value={criterion.key}
                        onChange={(e) => updateCriterion(i, "key", e.target.value)}
                        placeholder="Критерий, например «edge-cases»"
                        aria-label={`Критерий ${i + 1}: ключ`}
                        className={`${inputClasses} flex-1`}
                      />
                      <button
                        type="button"
                        onClick={() => removeCriterion(i)}
                        disabled={rubric.length <= MIN_RUBRIC_ITEMS}
                        aria-label={`Удалить критерий ${i + 1}`}
                        className="battle-press min-h-11 px-3 text-xs text-neutral-500 hover:text-red-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >
                        Убрать
                      </button>
                    </div>
                    <textarea
                      value={criterion.description}
                      onChange={(e) => updateCriterion(i, "description", e.target.value)}
                      placeholder="Что именно должно быть в ответе, чтобы критерий засчитался"
                      aria-label={`Критерий ${i + 1}: описание`}
                      className={`${inputClasses} min-h-16 py-2 resize-y leading-5`}
                    />
                  </div>
                );
              })}
            </div>
            <button
              type="button"
              onClick={addCriterion}
              disabled={rubric.length >= MAX_RUBRIC_ITEMS}
              className="battle-press mt-3 min-h-9 rounded-lg border border-neutral-700 px-3 text-xs font-medium text-neutral-300 hover:bg-white/[0.03] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              + Добавить критерий
            </button>
            <div className="min-h-5 mt-1.5 text-xs text-red-400">
              {rubricInvalid && "Каждый критерий должен иметь и ключ, и описание"}
            </div>
          </div>

          <div className="p-5 sm:p-6">
            <label htmlFor={categoryId} className="block text-sm font-medium text-neutral-200 mb-1.5">
              Категория
            </label>
            <input
              id={categoryId}
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              maxLength={MAX_CATEGORY_CHARS}
              aria-invalid={categoryInvalid || undefined}
              aria-describedby={categoryInvalid ? `${categoryId}-error` : undefined}
              className={`${inputClasses} ${categoryInvalid ? "border-red-500/40" : ""}`}
              placeholder="Например: backend"
            />
            <div id={`${categoryId}-error`} className="min-h-5 mt-1 text-xs text-red-400">
              {categoryInvalid && "Категория обязательна и не длиннее 50 символов"}
            </div>
          </div>

          <div className="p-5 sm:p-6" aria-invalid={difficultyInvalid || undefined}>
            <div className="mb-1.5 text-sm font-medium text-neutral-200">Сложность</div>
            <div role="radiogroup" aria-label="Сложность" className="flex flex-wrap gap-2">
              {DIFFICULTIES.map((d) => (
                <button
                  key={d}
                  type="button"
                  role="radio"
                  aria-checked={difficulty === d}
                  onClick={() => setDifficulty(d)}
                  className={`battle-press min-h-9 rounded-lg border px-3 text-xs font-medium transition-colors ${
                    difficulty === d
                      ? "border-cyan-500/50 bg-cyan-500/[0.08] text-cyan-300"
                      : "border-neutral-700 text-neutral-400 hover:text-neutral-200 hover:bg-white/[0.03]"
                  }`}
                >
                  {BATTLE_DIFFICULTY[d]}
                </button>
              ))}
            </div>
            <div className="min-h-5 mt-1.5 text-xs text-red-400">
              {difficultyInvalid && "Выберите сложность"}
            </div>
          </div>
        </div>

        <div className="mt-6 flex justify-end">
          <button
            onClick={submit}
            disabled={submitting || (touched && formInvalid)}
            className="battle-press min-h-11 w-full sm:w-auto rounded-lg bg-violet-600 hover:bg-violet-500 disabled:bg-neutral-800 disabled:text-neutral-500 disabled:cursor-not-allowed px-6 text-sm font-medium text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
          >
            {submitting ? (
              <span className="inline-flex items-center gap-2">
                <span className="h-3 w-3 rounded-full border-[1.5px] border-white/40 border-t-white animate-spin" />
                Отправляем…
              </span>
            ) : (
              "Отправить задачу"
            )}
          </button>
        </div>
      </main>
    </div>
  );
}
