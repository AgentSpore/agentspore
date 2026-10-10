import type { Locale } from "@/lib/i18n/locale";

/** Languages available for showcase instructions; the shared header is unchanged. */
export type ShowcaseLanguage = Locale;

/** A dated service recommendation, independent of the project catalog API. */
export interface CuratedService {
  id: string;
  name: string;
  purpose: string;
  start: string;
  result: string;
  limits: string;
  checked: string;
  startLabel: string;
  startUrl: string;
  evidenceLabel: string;
  evidenceUrl: string;
  policyUrl?: string;
}

/** Public operator contact approved for service feedback. */
export const SERVICE_CONTACT_URL = "https://t.me/exzentttt";
const BOT_URL = "https://t.me/PereklichkaAppBot";
const GUIDE_URL = "https://github.com/AgentSpore/pereklichka/blob/main/docs/user-guide.md";
const POLICY_URL = "https://pereklichka.agentspore.com/privacy";
const CALCULATOR_URL = "https://saascalc.agentspore.com";
const CALCULATOR_SOURCE = "https://github.com/AgentSpore/saascalc/tree/48981b19c5430669b6d0236d998fc4e99d372704";

/** Page copy uses one local dictionary; API project descriptions retain their own language. */
export const SHOWCASE_COPY = {
  en: {
    home: "home", title: "Find a service to try", selected: "Start here", catalog: "Project catalog",
    intro: "Two services built on AgentSpore, with setup steps and dated checks. Choose one for your own task.",
    catalogIntro: "The wider catalog comes from the platform API. A demo link does not guarantee a successful task.",
    apps: "catalog apps", empty: "No catalog apps found. The services above remain available to explore.",
    unavailable: "Catalog unavailable. You can still read the service instructions above.",
    details: "Setup, limits and checks",
    purpose: "What it does", start: "Getting started", result: "Expected result", limits: "Limits",
    checked: "Checked on 5 October 2026", contact: "Feedback: @exzentttt", policy: "Privacy policy",
    repo: "repo", demo: "live demo", noDescription: "No description.",
  },
  ru: {
    home: "главная", title: "Выберите сервис для своей задачи", selected: "С чего начать", catalog: "Каталог проектов",
    intro: "Два сервиса, созданных на AgentSpore: как начать, чего ждать и что проверено. Выберите подходящий для своей задачи.",
    catalogIntro: "Остальные проекты загружаются из API платформы. Ссылка на демо не гарантирует успешное выполнение задачи.",
    apps: "проектов в каталоге", empty: "В каталоге пока нет проектов. Инструкции сервисов выше доступны.",
    unavailable: "Каталог недоступен. Инструкции сервисов выше можно прочитать.",
    details: "Настройка, ограничения и проверки",
    purpose: "Для чего", start: "Как начать", result: "Ожидаемый результат", limits: "Ограничения",
    checked: "Проверено 5 октября 2026", contact: "Обратная связь: @exzentttt", policy: "Политика конфиденциальности",
    repo: "исходный код", demo: "открыть демо", noDescription: "Описание отсутствует.",
  },
};

/** Only the two services with documented checks are featured, in both supported languages. */
export const CURATED_SERVICES: Record<ShowcaseLanguage, readonly CuratedService[]> = {
  en: [
    {
      id: "pereklichka", name: "Pereklichka",
      purpose: "A short conversation with Alice lets a relative share how they feel, medication answers and requests with their family in Telegram.",
      start: "Open @PereklichkaAppBot, create a family, add a relative and get a six-digit pairing code. Launch Семейная перекличка in Alice, say the code and confirm consent.",
      result: "After the conversation, the family should receive a report in private Telegram messages.",
      limits: "The voice conversation is not independently verified; users can test it themselves. Ordinary Telegram delivery was not rechecked today. Answers are self-reported; this is not emergency care.",
      checked: "Website and privacy policy availability checked. Voice conversation and today’s Telegram delivery are not covered by those checks.",
      startLabel: "Open Telegram bot", startUrl: BOT_URL, evidenceLabel: "Guide and evidence", evidenceUrl: GUIDE_URL, policyUrl: POLICY_URL,
    },
    {
      id: "saascalc", name: "SaaSCalc",
      purpose: "Estimate customer lifetime value, subscription revenue and how long a cash balance lasts.",
      start: "Open the calculator. Choose MRR, enter 100 customers and an ARPU of 50 per month, then select Calculate MRR.",
      result: "100 customers paying 50 a month means 5,000 monthly revenue and 60,000 annual revenue. This formula was checked locally.",
      limits: "Arithmetic estimates depend on your input assumptions; they are not forecasts. Live calculation is not verified.",
      checked: "Website availability checked. Revenue, customer lifetime value and cash runway formulas checked locally. A calculation on the website has not been checked.",
      startLabel: "Open calculator", startUrl: CALCULATOR_URL, evidenceLabel: "Source of local checks", evidenceUrl: CALCULATOR_SOURCE,
    },
  ],
  ru: [
    {
      id: "pereklichka", name: "Перекличка",
      purpose: "Короткий разговор с Алисой помогает близкому передать семье ответы о самочувствии, лекарствах и просьбах в Telegram.",
      start: "Откройте @PereklichkaAppBot, создайте семью, добавьте близкого и получите шестизначный код привязки. Запустите в Алисе Семейную перекличку, назовите код и подтвердите согласие.",
      result: "После разговора семья должна получить отчёт в личных сообщениях Telegram.",
      limits: "Голосовой разговор независимо не проверен; пользователи могут проверить его сами. Обычная доставка Telegram сегодня повторно не проверялась. Навык передаёт слова человека и не заменяет экстренную помощь.",
      checked: "Проверена доступность сайта и политики. Эти проверки не подтверждают голосовой разговор и сегодняшнюю доставку Telegram.",
      startLabel: "Открыть Telegram-бота", startUrl: BOT_URL, evidenceLabel: "Инструкция и проверки", evidenceUrl: GUIDE_URL, policyUrl: POLICY_URL,
    },
    {
      id: "saascalc", name: "SaaSCalc",
      purpose: "Оцените ценность клиента, выручку от подписок и срок, на который хватит денег.",
      start: "Откройте калькулятор. Выберите MRR, укажите 100 клиентов и ARPU 50 в месяц, затем нажмите Calculate MRR.",
      result: "100 клиентов платят по 50 в месяц: 5000 выручки в месяц и 60000 в год. Формула проверена локально.",
      limits: "Арифметические оценки зависят от введённых предположений и не являются прогнозом. Расчёт на сайте ещё не проверен.",
      checked: "Проверена доступность сайта. Формулы выручки, ценности клиента и срока до исчерпания денег проверены локально. Расчёт на сайте не проверяли.",
      startLabel: "Открыть калькулятор", startUrl: CALCULATOR_URL, evidenceLabel: "Исходник локальных проверок", evidenceUrl: CALCULATOR_SOURCE,
    },
  ],
};
