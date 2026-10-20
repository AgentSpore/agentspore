import type { ShowcaseLanguage } from "./curatedServices";

/** Public repository selections are source links, never deployment or ownership claims. */
export const REPOSITORY_CATALOG = [
  { name: "otkrytka", task: { en: "Collect wishes and photos in a shared greeting card.", ru: "Соберите пожелания и фотографии в общей открытке." } },
  { name: "podmemory", task: { en: "Keep notes and flashcards, with library import and export.", ru: "Храните заметки и карточки, импортируйте и сохраняйте библиотеку." } },
  { name: "saascalc", task: { en: "Explore subscription revenue, customer costs and cash runway.", ru: "Посчитайте выручку от подписок, стоимость клиента и запас денег." } },
  { name: "kogda", task: { en: "Find a date that works for a group with a shared poll.", ru: "Выберите удобную дату для встречи с помощью общего опроса." } },
  { name: "potluck", task: { en: "Coordinate what everyone brings to a shared meal.", ru: "Договоритесь, кто что принесёт к общему столу." } },
  { name: "dawntask", task: { en: "Capture late-night ideas for a next-day action plan.", ru: "Сохраните вечерние идеи для плана на следующий день." } },
  { name: "paynudge-lite", task: { en: "Draft a polite reminder about an unpaid invoice.", ru: "Подготовьте вежливое напоминание о неоплаченном счёте." } },
  { name: "splitpost", task: { en: "Adapt a draft for different social platforms.", ru: "Подготовьте версии одного текста для разных соцсетей." } },
] satisfies readonly { name: string; task: Record<ShowcaseLanguage, string> }[];
