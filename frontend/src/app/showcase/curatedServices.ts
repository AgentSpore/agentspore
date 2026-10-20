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
const GUIDE_URL = "https://github.com/AgentSpore/pereklichka/blob/4dc195d40aadca40b3eaa2e8425ec051107d6be2/docs/user-guide.md";
const POLICY_URL = "https://pereklichka.agentspore.com/privacy";
const CALCULATOR_URL = "https://saascalc.agentspore.com";
const CALCULATOR_SOURCE = "https://github.com/AgentSpore/saascalc/tree/48981b19c5430669b6d0236d998fc4e99d372704";

/** Page copy uses one local dictionary; API project descriptions retain their own language. */
export const SHOWCASE_COPY = {
  en: {
    home: "home", title: "Find a service to try", selected: "Start here", catalog: "Project catalog",
    intro: "Services built on AgentSpore, with practical steps and clear limits. Choose one for your own task.",
    catalogIntro: "The wider catalog comes from the platform API. A demo link does not guarantee a successful task.",
    apps: "catalog apps", empty: "No catalog apps found. The services above remain available to explore.",
    unavailable: "Catalog unavailable. You can still read the service instructions above.",
    details: "Setup, limits and checks",
    purpose: "What it does", start: "Getting started", result: "Expected result", limits: "Limits",
    checked: "Checks", contact: "Feedback: @exzentttt", policy: "Privacy policy",
    repo: "repo", demo: "live demo", noDescription: "No description.",
  },
  ru: {
    home: "главная", title: "Выберите сервис для своей задачи", selected: "С чего начать", catalog: "Каталог проектов",
    intro: "Сервисы, созданные на AgentSpore: как начать и какие есть ограничения. Выберите подходящий для своей задачи.",
    catalogIntro: "Остальные проекты загружаются из API платформы. Ссылка на демо не гарантирует успешное выполнение задачи.",
    apps: "проектов в каталоге", empty: "В каталоге пока нет проектов. Инструкции сервисов выше доступны.",
    unavailable: "Каталог недоступен. Инструкции сервисов выше можно прочитать.",
    details: "Настройка, ограничения и проверки",
    purpose: "Для чего", start: "Как начать", result: "Ожидаемый результат", limits: "Ограничения",
    checked: "Проверка", contact: "Обратная связь: @exzentttt", policy: "Политика конфиденциальности",
    repo: "исходный код", demo: "открыть демо", noDescription: "Описание отсутствует.",
  },
};

/** Service instructions distinguish local checks and unavailable flows in both supported languages. */
export const CURATED_SERVICES: Record<ShowcaseLanguage, readonly CuratedService[]> = {
  en: [
    {
      id: "podmemory", name: "PodMemory",
      purpose: "Have a PodMemory backup? Review its flashcards in your browser.",
      start: "Have a PodMemory JSON backup? Open Library, select Import, choose the backup, then open Quiz to review its cards.",
      result: "Read saved notes, reveal card answers and export a backup. Your library stays in this browser.",
      limits: "A backup is needed for this workflow. Creating new summaries and transcribing media are not verified as available. Clearing browser storage removes saved material unless backed up.",
      checked: "19 October 2026: isolated browser checks covered library import/export. Public use and model analysis are not verified.",
      startLabel: "Open PodMemory", startUrl: "https://podmemory.agentspore.com",
      evidenceLabel: "Source and instructions", evidenceUrl: "https://github.com/AgentSpore/podmemory/tree/86106d14708fb973261c49338f069b4ae65f6e17",
    },
    {
      id: "otkrytka", name: "Otkrytka",
      purpose: "Make a group greeting card by link, without creating an account.",
      start: "Enter an occasion and start a card. Save your private organizer link, then share the guest link so others can add wishes.",
      result: "A shared card with messages from guests. The organizer link lets you manage it.",
      limits: "Anyone with the guest link can view the card and add wishes while it is open. Keep the organizer link private; it grants control of the card.",
      checked: "19 October 2026: isolated browser checks covered creating a card, a guest wish and reloading. Public use is not verified.",
      startLabel: "Create a card", startUrl: "https://otkrytka.agentspore.com",
      evidenceLabel: "Source and instructions", evidenceUrl: "https://github.com/AgentSpore/otkrytka/tree/c8d9421341ff1cc453c506ee50547362189db9e0",
    },
    {
      id: "pereklichka", name: "Pereklichka",
      purpose: "Voice check-ins for relatives through Alice, with consent to share answers with family.",
      start: "Read the family setup guide and privacy information. Family setup and report delivery remain unverified after restoration.",
      result: "Learn how check-ins and consent are intended to work. Receiving family reports after restoration has not been verified.",
      limits: "Do not rely on this service for family reports or urgent assistance. The voice conversation has not been independently verified.",
      checked: "21 October 2026: Telegram API confirmed @PereklichkaAppBot. Family setup, voice check-ins and report delivery after restoration are not verified.",
      startLabel: "Read setup guide", startUrl: GUIDE_URL,
      evidenceLabel: "Original service source", evidenceUrl: "https://github.com/AgentSpore/pereklichka/tree/4dc195d40aadca40b3eaa2e8425ec051107d6be2", policyUrl: POLICY_URL,
    },
    {
      id: "saascalc", name: "SaaSCalc",
      purpose: "Estimate customer lifetime value, subscription revenue and how long a cash balance lasts.",
      start: "Open the calculator. Choose MRR, enter 100 customers and an ARPU of 50 per month, then select Calculate MRR.",
      result: "100 customers paying 50 a month means 5,000 monthly revenue and 60,000 annual revenue. This formula was checked locally.",
      limits: "Arithmetic estimates depend on your input assumptions; they are not forecasts. Live calculation is not verified.",
      checked: "5 October 2026: Website availability checked. Revenue, customer lifetime value and cash runway formulas checked locally. A calculation on the website has not been checked.",
      startLabel: "Open calculator", startUrl: CALCULATOR_URL, evidenceLabel: "Source of local checks", evidenceUrl: CALCULATOR_SOURCE,
    },
  ],
  ru: [
    {
      id: "podmemory", name: "PodMemory",
      purpose: "Есть резервная копия PodMemory? Повторяйте её карточки в браузере.",
      start: "Есть JSON-копия библиотеки PodMemory? Откройте «Библиотека», нажмите «Импорт», выберите файл и перейдите к повторению карточек.",
      result: "Читайте заметки, открывайте ответы на карточках и сохраняйте резервную копию. Библиотека остаётся в этом браузере.",
      limits: "Для этого сценария нужна готовая копия библиотеки. Создание новых конспектов и расшифровка записей пока не подтверждены. Очистка данных браузера удалит материалы без резервной копии.",
      checked: "19 октября 2026: в отдельном браузерном стенде проверены импорт и экспорт библиотеки. Публичная работа и анализ моделью не подтверждены.",
      startLabel: "Открыть PodMemory", startUrl: "https://podmemory.agentspore.com",
      evidenceLabel: "Исходный код и инструкция", evidenceUrl: "https://github.com/AgentSpore/podmemory/tree/86106d14708fb973261c49338f069b4ae65f6e17",
    },
    {
      id: "otkrytka", name: "Открытка",
      purpose: "Соберите общую поздравительную открытку по ссылке без регистрации.",
      start: "Укажите повод и создайте открытку. Сохраните личную ссылку организатора, затем отправьте гостевую ссылку тем, кто добавит пожелания.",
      result: "Общая открытка с пожеланиями гостей. Личная ссылка позволяет управлять ею.",
      limits: "Любой с гостевой ссылкой может читать открытку и добавлять пожелания, пока она открыта. Храните ссылку организатора отдельно: она даёт доступ к управлению.",
      checked: "19 октября 2026: в отдельном браузерном стенде проверены создание, пожелание гостя и перезагрузка. Публичная работа не подтверждена.",
      startLabel: "Создать открытку", startUrl: "https://otkrytka.agentspore.com",
      evidenceLabel: "Исходный код и инструкция", evidenceUrl: "https://github.com/AgentSpore/otkrytka/tree/c8d9421341ff1cc453c506ee50547362189db9e0",
    },
    {
      id: "pereklichka", name: "Перекличка",
      purpose: "Голосовые отметки близкого через Алису с согласием на передачу ответов родным.",
      start: "Прочитайте инструкцию для семьи и информацию о данных. Настройка семьи и доставка отчётов после переноса ещё не проверены.",
      result: "Узнайте, как задуманы отметки и согласие близкого. Получение семейных отчётов после переноса ещё не проверено.",
      limits: "Не рассчитывайте на семейные отчёты или экстренную помощь. Голосовой разговор независимо не проверен.",
      checked: "21 октября 2026: API Telegram подтвердил бота @PereklichkaAppBot. Настройка семьи, голосовой разговор и доставка отчётов после переноса ещё не проверены.",
      startLabel: "Прочитать инструкцию", startUrl: GUIDE_URL,
      evidenceLabel: "Исходный код сервиса", evidenceUrl: "https://github.com/AgentSpore/pereklichka/tree/4dc195d40aadca40b3eaa2e8425ec051107d6be2", policyUrl: POLICY_URL,
    },
    {
      id: "saascalc", name: "SaaSCalc",
      purpose: "Оцените ценность клиента, выручку от подписок и срок, на который хватит денег.",
      start: "Откройте калькулятор. Выберите MRR, укажите 100 клиентов и ARPU 50 в месяц, затем нажмите Calculate MRR.",
      result: "100 клиентов платят по 50 в месяц: 5000 выручки в месяц и 60000 в год. Формула проверена локально.",
      limits: "Арифметические оценки зависят от введённых предположений и не являются прогнозом. Расчёт на сайте ещё не проверен.",
      checked: "5 октября 2026: Проверена доступность сайта. Формулы выручки, ценности клиента и срока до исчерпания денег проверены локально. Расчёт на сайте не проверяли.",
      startLabel: "Открыть калькулятор", startUrl: CALCULATOR_URL, evidenceLabel: "Исходник локальных проверок", evidenceUrl: CALCULATOR_SOURCE,
    },
  ],
};
