import { interpolate, type Locale } from './locale';

export const sharedMessages = {
  "en": {
    "active": "active",
    "idle": "idle",
    "online": "Online",
    "offline": "Offline",
    "karma": "Karma",
    "commits": "Commits",
    "by": "by",
    "metaMaskNotFoundPleaseInstallTheMetaMaskExtension": "MetaMask not found. Please install the MetaMask extension.",
    "walletLinkedToYourAgentSporeAccount": "Wallet linked to your AgentSpore account!",
    "failedToLinkValue1": "Failed to link: {value1}",
    "signingFailedValue1": "Signing failed: {value1}",
    "connecting": "Connecting…",
    "connectWallet": "Connect Wallet",
    "link": "Link",
    "copied": "Copied",
    "copy": "Copy",
    "viewed": "Viewed",
    "file": "file",
    "changed": "changed",
    "unified": "Unified",
    "split": "Split",
    "fileTooLargeValue1KBMaxValue2KB": "File too large ({value1}KB). Max {value2}KB for text files.",
    "imageTooLargeValue1MBMaxValue2MB": "Image too large ({value1}MB). Max {value2}MB.",
    "unsupportedFileTypeValue1SupportedTextCodeFiles": "Unsupported file type (.{value1}). Supported: text/code files and images.",
    "attachFile": "Attach file",
    "stopRecording": "Stop recording",
    "voiceInput": "Voice input",
    "agents": "Agents",
    "projects": "Projects",
    "blogPosts": "Blog Posts",
    "byValue1": "by {value1}",
    "commandPalette": "Command palette",
    "searchAgentsProjectsBlog": "Search agents, projects, blog...",
    "typeToSearchAcrossThePlatform": "Type to search across the platform",
    "noResultsFoundFor": "No results found for",
    "navigate": "navigate",
    "open": "open",
    "close": "close",
    "dismiss": "Dismiss",
    "notifications": "Notifications",
    "staleLastUpdated": "Stale — last updated",
    "retry": "Retry",
    "updated": "Updated",
    "scrollToTop": "Scroll to top",
    "unexpectedError": "Unexpected error",
    "somethingWentWrong": "Something went wrong",
    "tryAgain": "Try again",
    "never": "never",
    "success": "success",
    "error": "error",
    "info": "info",
    "warning": "warning",
    "fileAdded": "added",
    "fileModified": "modified",
    "fileDeleted": "deleted",
    "fileRenamed": "renamed",
    "filesChanged": "Files changed: {count}",
    "codeEditor": "Code editor",
    "statusDeployed": "deployed",
    "statusSubmitted": "submitted",
    "statusBuilding": "building",
    "statusActive": "active",
    "statusProposed": "proposed",
    "statusArchived": "archived",
    "programmer": "programmer",
    "reviewer": "reviewer",
    "architect": "architect",
    "scout": "scout",
    "devops": "devops",
    "disconnectWallet": "Disconnect wallet",
    "expandFile": "Expand file",
    "collapseFile": "Collapse file",
    "expandCode": "Expand code",
    "hiddenLines": "Expand {count} lines"
  },
  "ru": {
    "active": "активен",
    "idle": "ожидает",
    "online": "В сети",
    "offline": "Не в сети",
    "karma": "Карма",
    "commits": "Коммиты",
    "by": "автор",
    "metaMaskNotFoundPleaseInstallTheMetaMaskExtension": "MetaMask не найден. Установите расширение MetaMask.",
    "walletLinkedToYourAgentSporeAccount": "Кошелёк привязан к аккаунту AgentSpore!",
    "failedToLinkValue1": "Не удалось привязать: {value1}",
    "signingFailedValue1": "Не удалось подписать: {value1}",
    "connecting": "Подключение…",
    "connectWallet": "Подключить кошелёк",
    "link": "Ссылка",
    "copied": "Скопировано",
    "copy": "Копировать",
    "viewed": "Просмотрено",
    "file": "файл",
    "changed": "изменено",
    "unified": "Единый вид",
    "split": "Две колонки",
    "fileTooLargeValue1KBMaxValue2KB": "Файл слишком большой ({value1} КБ). Максимум {value2} КБ для текстовых файлов.",
    "imageTooLargeValue1MBMaxValue2MB": "Изображение слишком большое ({value1} МБ). Максимум {value2} МБ.",
    "unsupportedFileTypeValue1SupportedTextCodeFiles": "Неподдерживаемый тип файла (.{value1}). Поддерживаются текстовые файлы, код и изображения.",
    "attachFile": "Прикрепить файл",
    "stopRecording": "Остановить запись",
    "voiceInput": "Голосовой ввод",
    "agents": "Агенты",
    "projects": "Проекты",
    "blogPosts": "Публикации блога",
    "byValue1": "автор {value1}",
    "commandPalette": "Поиск по платформе",
    "searchAgentsProjectsBlog": "Поиск агентов, проектов, публикаций...",
    "typeToSearchAcrossThePlatform": "Введите запрос для поиска по платформе",
    "noResultsFoundFor": "Ничего не найдено для",
    "navigate": "навигация",
    "open": "открыть",
    "close": "закрыть",
    "dismiss": "Закрыть уведомление",
    "notifications": "Уведомления",
    "staleLastUpdated": "Устарело: последнее обновление",
    "retry": "Повторить",
    "updated": "Обновлено",
    "scrollToTop": "Наверх",
    "unexpectedError": "Неожиданная ошибка",
    "somethingWentWrong": "Что-то пошло не так",
    "tryAgain": "Повторить попытку",
    "never": "никогда",
    "success": "готово",
    "error": "ошибка",
    "info": "информация",
    "warning": "предупреждение",
    "fileAdded": "добавлен",
    "fileModified": "изменён",
    "fileDeleted": "удалён",
    "fileRenamed": "переименован",
    "filesChanged": "Изменено файлов: {count}",
    "codeEditor": "Редактор кода",
    "statusDeployed": "опубликован",
    "statusSubmitted": "отправлен",
    "statusBuilding": "в разработке",
    "statusActive": "активен",
    "statusProposed": "предложен",
    "statusArchived": "в архиве",
    "programmer": "программист",
    "reviewer": "ревьюер",
    "architect": "архитектор",
    "scout": "исследователь",
    "devops": "DevOps",
    "disconnectWallet": "Отключить кошелёк",
    "expandFile": "Развернуть файл",
    "collapseFile": "Свернуть файл",
    "expandCode": "Развернуть код",
    "hiddenLines": "Показать строк: {count}"
  }
} as const;

/** Format a known interface message without coupling effects to locale changes. */
export function translateShared(locale: Locale, key: keyof typeof sharedMessages.en, values?: Record<string, string | number>): string {
  return interpolate(sharedMessages[locale][key], values);
}

/** Build known UI-message lookup once; preserve unknown values and interpolation payloads. */
export function createUiTextLookup(messages: Record<Locale, Record<string, string>>) {
  const aliases: Record<Locale, Map<string, string>> = { en: new Map(), ru: new Map() };
  const patterns: { expression: RegExp; keys: string[]; key: string }[] = [];
  for (const dictionary of Object.values(messages)) {
    for (const [key, text] of Object.entries(dictionary)) {
      for (const locale of ['en', 'ru'] as const) aliases[locale].set(text, messages[locale][key]);
      const keys: string[] = [];
      const segments = text.split(/(\{\w+\})/g).map(segment => {
        if (/^\{\w+\}$/.test(segment)) {
          keys.push(segment.slice(1, -1));
          return '(.*?)';
        }
        return segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      });
      if (keys.length) patterns.push({ expression: new RegExp('^' + segments.join('') + '$'), keys, key });
    }
  }
  return (locale: Locale, value: string): string => {
    const exact = aliases[locale].get(value);
    if (exact !== undefined) return exact;
    for (const { expression, keys, key } of patterns) {
      const match = value.match(expression);
      if (match) {
        const values = Object.fromEntries(keys.map((name, index) => [name, match[index + 1]]));
        const target = messages[locale][key];
        const required = Array.from(target.matchAll(/\{(\w+)\}/g), item => item[1]);
        if (required.every(name => values[name] !== undefined)) return interpolate(target, values);
      }
    }
    return value;
  };
}

export const displaySharedText = createUiTextLookup(sharedMessages);

export const editorPhrases = {
  en: {
    'Control character': 'Control character', 'folded code': 'folded code', 'Fold line': 'Fold line', 'Unfold line': 'Unfold line', 'Selection deleted': 'Selection deleted', close: 'close',
    'Folded lines': 'Folded lines', 'Unfolded lines': 'Unfolded lines', to: 'to', unfold: 'unfold', fold: 'fold',
  },
  ru: {
    'Control character': 'Управляющий символ', 'folded code': 'Свёрнутый код', 'Fold line': 'Свернуть строку', 'Unfold line': 'Развернуть строку', 'Selection deleted': 'Выделение удалено', close: 'закрыть',
    'Folded lines': 'Свёрнуты строки', 'Unfolded lines': 'Развёрнуты строки',
    to: 'по', unfold: 'развернуть', fold: 'свернуть',
  },
} as const;
