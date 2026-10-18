const LANGUAGE_NAMES = {
  "en": "English",
  "ru": "Русский",
  "es": "Español",
  "pt": "Português",
  "fr": "Français",
  "de": "Deutsch",
  "it": "Italiano",
  "nl": "Nederlands",
  "pl": "Polski",
  "uk": "Українська",
  "cs": "Čeština",
  "sk": "Slovenčina",
  "ro": "Română",
  "hu": "Magyar",
  "el": "Ελληνικά",
  "tr": "Türkçe",
  "ar": "العربية",
  "he": "עברית",
  "fa": "فارسی",
  "hi": "हिन्दी",
  "bn": "বাংলা",
  "ur": "اردو",
  "id": "Bahasa Indonesia",
  "ms": "Bahasa Melayu",
  "vi": "Tiếng Việt",
  "th": "ไทย",
  "ko": "한국어",
  "ja": "日本語",
  "zh-Hans": "简体中文",
  "sw": "Kiswahili",
  "tl": "Tagalog",
};
const LANGUAGES = new Set(Object.keys(LANGUAGE_NAMES));
const RTL = new Set(["ar", "he", "fa", "ur"]);
const element = (id) => document.getElementById(id);
const translations = new Map();
const segmenter = typeof Intl.Segmenter === "function"
  ? new Intl.Segmenter(undefined, { granularity: "grapheme" }) : null;
let locale = "en";
let copy;
let curriculum;
let attempt;
let roundIndex = 0;
let answered = false;
let localeRequest = 0;

function formatText(key, values = {}) {
  return copy.ui[key].replace(/\{(\w+)\}/g, (token, name) => values[name] ?? token);
}

async function loadContent(language) {
  if (!LANGUAGES.has(language)) throw new Error("unsupported_language");
  if (translations.has(language)) return translations.get(language);
  const response = await fetch(`content/${language}.json`, { credentials: "same-origin" });
  if (!response.ok) throw new Error("language_unavailable");
  const content = await response.json();
  translations.set(language, content);
  return content;
}

function updateShareLinks() {
  const appURL = new URL(location.pathname, location.origin);
  appURL.searchParams.set("lang", locale);
  const whatsapp = new URL("https://wa.me/");
  whatsapp.searchParams.set("text", `${copy.ui.shareText} ${appURL.href}`);
  const telegram = new URL("https://t.me/share/url");
  telegram.searchParams.set("url", appURL.href);
  telegram.searchParams.set("text", copy.ui.shareText);
  element("share-whatsapp").href = whatsapp.href;
  element("share-telegram").href = telegram.href;
}

function validatePhrase() {
  const value = element("family-phrase").value;
  let valid = !value || Boolean(segmenter);
  if (value && segmenter) {
    let length = 0;
    for (const _ of segmenter.segment(value)) {
      length += 1;
      if (length > 80) {
        valid = false;
        break;
      }
    }
  }
  element("phrase-error").textContent = copy.ui[segmenter ? "phraseTooLong" : "phraseUnsupported"];
  element("phrase-error").hidden = valid;
  element("family-phrase").setAttribute("aria-invalid", String(!valid));
  element("print-card").disabled = !valid;
  const include = valid && element("include-phrase").checked && Boolean(value.trim());
  element("card-phrase-area").hidden = !include;
  element("card-phrase").textContent = include ? value : "";
  return valid;
}

function renderFeedback() {
  const correct = attempt.answers[roundIndex];
  const explanation = copy.rounds[curriculum.rounds[roundIndex].id].explanation;
  element("round-feedback").textContent = `${copy.ui[correct ? "correct" : "incorrect"]} ${explanation}`;
}

function renderRound() {
  const round = curriculum.rounds[roundIndex];
  element("training").dataset.roundId = String(round.id);
  element("round-status").textContent = formatText("roundStatus", {
    current: roundIndex + 1, total: curriculum.rounds.length,
  });
  element("round-message").textContent = copy.rounds[round.id].message;
  element("round-feedback").hidden = !answered;
  element("pause-verify").disabled = answered;
  element("ordinary-request").disabled = answered;
  element("next-round").disabled = !answered;
  element("next-round").textContent = copy.ui[roundIndex === 9 ? "finishTraining" : "nextRound"];
  if (answered) renderFeedback();
}

function renderResult() {
  element("result-score").textContent = formatText("resultScore", {
    score: attempt.answers.filter(Boolean).length, total: curriculum.rounds.length,
  });
}

function renderLanguage() {
  document.title = copy.ui.title;
  document.documentElement.lang = locale;
  document.documentElement.dir = RTL.has(locale) ? "rtl" : "ltr";
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = copy.ui[node.dataset.i18n];
  });
  element("load-error").textContent = copy.ui.loadError;
  updateShareLinks();
  validatePhrase();
  if (attempt) {
    if (element("training").hidden) renderResult();
    else renderRound();
  }
}

async function changeLanguage() {
  const requested = element("language").value;
  const request = ++localeRequest;
  try {
    const nextCopy = await loadContent(requested);
    if (request !== localeRequest) return;
    locale = requested;
    copy = nextCopy;
    element("load-error").hidden = true;
    renderLanguage();
  } catch {
    if (request !== localeRequest) return;
    element("language").value = locale;
    element("load-error").hidden = false;
  }
}

function recordProgress() {
  const payload = {
    event: "training_progress",
    attempt_id: attempt.id,
    curriculum_version: attempt.version,
    lang: attempt.lang,
    answers: [...attempt.answers],
    completed: attempt.answers.length === curriculum.rounds.length,
  };
  // D1 metrics are best-effort: network failure must never block practice or print.
  void fetch("/_agentspore/events", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(3000),
  }).catch(() => undefined);
}

function startTraining() {
  attempt = { id: crypto.randomUUID(), version: curriculum.version, lang: locale, answers: [] };
  roundIndex = 0;
  answered = false;
  element("start-training").hidden = true;
  element("training-result").hidden = true;
  element("restart-training").hidden = false;
  element("training").hidden = false;
  renderRound();
  recordProgress();
  element("round-status").focus();
}

function answerRound(pause) {
  if (!attempt || answered || element("training").hidden) return;
  answered = true;
  attempt.answers.push(pause === curriculum.rounds[roundIndex].correct_pause);
  renderRound();
  recordProgress();
}

function nextRound() {
  if (!answered || element("training").hidden) return;
  if (roundIndex === curriculum.rounds.length - 1) {
    element("training").hidden = true;
    element("training-result").hidden = false;
    renderResult();
    element("training-result").focus();
    return;
  }
  roundIndex += 1;
  answered = false;
  renderRound();
  element("round-status").focus();
}

async function initialize() {
  const selector = element("language");
  const options = Object.entries(LANGUAGE_NAMES).map(([code, name]) => {
    const option = new Option(name, code);
    option.lang = code;
    return option;
  });
  selector.replaceChildren(...options);
  selector.value = locale;
  try {
    const [english, response] = await Promise.all([
      loadContent("en"), fetch("content/curriculum.json", { credentials: "same-origin" }),
    ]);
    if (!response.ok) throw new Error("curriculum_unavailable");
    curriculum = await response.json();
    copy = english;
    renderLanguage();
    element("language").addEventListener("change", changeLanguage);
    element("family-phrase").addEventListener("input", validatePhrase);
    element("include-phrase").addEventListener("change", validatePhrase);
    element("print-card").addEventListener("click", () => {
      if (validatePhrase()) window.print();
    });
    element("start-training").addEventListener("click", startTraining);
    element("restart-training").addEventListener("click", startTraining);
    element("pause-verify").addEventListener("click", () => answerRound(true));
    element("ordinary-request").addEventListener("click", () => answerRound(false));
    element("next-round").addEventListener("click", nextRound);
    const preferred = new URLSearchParams(location.search).get("lang");
    if (LANGUAGES.has(preferred) && preferred !== locale) {
      selector.value = preferred;
      await changeLanguage();
    }
    element("start-training").disabled = false;
    selector.disabled = false;
  } catch {
    element("load-error").hidden = false;
  }
}

void initialize();
