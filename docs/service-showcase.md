# Choose a service / Выберите сервис

24.10.2026 · Accepted interface reference for the service-first homepage. Service checks retain their original dates; this guide does not certify live service availability.
Owner / Владелец: Roman Konnov · Review by / Пересмотреть до: 31.10.2026.

The home page introduces featured services. Choose **Services**, the first main navigation link, to open [the showcase](https://agentspore.com/showcase). Use [Projects](https://agentspore.com/projects) for the wider catalog and project history. On a phone, open the menu first. Choose EN or RU in the shared site header. Each featured card shows its purpose, expected result and start link. Expand **Setup, limits and checks** for instructions, dated checks, evidence links and feedback. These details remain readable when the project catalog fails to load.

| When / Когда | Input / Вход | Result / Результат | Limit / Граница |
|---|---|---|---|
| Choose EN/RU / Выбран EN/RU | Header EN/RU button / EN/RU в шапке | Page instructions change / Инструкции переводятся | API descriptions retain their original language / Описания API сохраняют исходный язык |
| Open a service / Открыт сервис | Start link / Ссылка запуска | Service or setup guide / Сервис или инструкция настройки | A link does not prove a completed task / Ссылка не подтверждает выполнение задачи |
| Read details / Открыты подробности | Expand summary / Раскрыта строка подробностей | Setup, limits, checks and links / Настройка, ограничения, проверки и ссылки | Checked dates stay unchanged / Даты проверок сохраняются |
| Catalog fails / Ошибка каталога | API error / Ошибка API | Featured cards remain / Подборка сохраняется | Retained catalog data may be stale / Сохранённый каталог может устареть |

These checks do not establish actual usage or returning visitors. / Эти проверки не подтверждают число пользователей или возвращающихся посетителей.

## English

### Otkrytka

Open [Otkrytka](https://otkrytka.agentspore.com), enter an occasion and create a group card. Keep the organizer link private; share only the guest link for wishes. Anyone with the guest link can read the card and add wishes while it is open. On 19 October, isolated browser checks covered creation, a guest wish and reloading; public use remains unverified. [Checked source and instructions](https://github.com/AgentSpore/otkrytka/tree/c8d9421341ff1cc453c506ee50547362189db9e0).

### PodMemory

Have a PodMemory JSON backup? Open [PodMemory](https://podmemory.agentspore.com), select Library, Import and your backup, then open Quiz. Library import, Quiz and export were checked on 21 October. Creating a new summary in the browser and audio flows remain unverified. Your library stays in this browser; clearing storage removes it unless backed up. [Checked source and instructions](https://github.com/AgentSpore/podmemory/tree/86106d14708fb973261c49338f069b4ae65f6e17).

### Pereklichka

Start with the [family setup guide](https://github.com/AgentSpore/pereklichka/blob/main/docs/user-guide.md) and [privacy policy](https://pereklichka.agentspore.com/privacy). The intended flow connects [@PereklichkaAppBot](https://t.me/PereklichkaAppBot) to Alice with a six-digit code and consent to share answers. On 21 October, Telegram API confirmed the bot identity. Family setup, voice check-ins and report delivery remain unverified after restoration. Do not rely on it for reports or urgent assistance.

### HISTORICAL: SaaSCalc, 5 October 2026

The following record preserves earlier checks; it does not establish current availability.

Open [SaaSCalc](https://saascalc.agentspore.com), choose MRR, enter 100 customers and an ARPU of 50 per month, then select Calculate MRR. The locally checked formula gives 5,000 monthly revenue and 60,000 annual revenue for those inputs. These are expected local results, not a verified production calculation.

The public home and health pages returned HTTP 200 on 5 October. [The checked source revision](https://github.com/AgentSpore/saascalc/tree/48981b19c5430669b6d0236d998fc4e99d372704) also gave LTV 800 and lifetime 20 months for ARPU 50, churn 5% and margin 80%; cash 60,000 with monthly burn 10,000 gave a six-month runway. These arithmetic estimates depend on input assumptions and are not forecasts. The public HTML differs from GitHub; whether the installed backend matches that source is UNKNOWN. Live calculation remains unverified.

## Русский

На главной странице показана подборка сервисов. Выберите **Сервисы**, первый пункт основной навигации, чтобы открыть [витрину](https://agentspore.com/showcase). В разделе [Проекты](https://agentspore.com/projects) доступны общий каталог и история проектов. На телефоне сначала откройте меню. Нажмите **RU** в общей шапке сайта. Карточка сразу показывает назначение, ожидаемый результат и ссылку запуска. Раскройте **Настройка, ограничения и проверки**, чтобы прочитать инструкцию, даты проверок и открыть ссылки на подтверждения и обратную связь. Эти сведения остаются доступны при ошибке каталога.

### Открытка

Откройте [Открытку](https://otkrytka.agentspore.com), укажите повод и создайте общую открытку. Ссылку организатора храните отдельно; гостям передавайте только ссылку для пожеланий. Любой с гостевой ссылкой может читать открытку и добавлять пожелания, пока она открыта. 19 октября в отдельном браузерном стенде проверены создание, пожелание гостя и перезагрузка; публичная работа не подтверждена. [Проверенный исходник и инструкция](https://github.com/AgentSpore/otkrytka/tree/c8d9421341ff1cc453c506ee50547362189db9e0).

### PodMemory

Есть JSON-копия библиотеки PodMemory? Откройте [PodMemory](https://podmemory.agentspore.com), выберите «Библиотека», «Импорт», свой файл и перейдите к повторению карточек. Импорт, повторение и экспорт библиотеки проверены 21 октября. Создание нового конспекта в браузере и работа с аудио ещё не проверены. Библиотека хранится в этом браузере; очистка данных удалит её без резервной копии. [Проверенный исходник и инструкция](https://github.com/AgentSpore/podmemory/tree/86106d14708fb973261c49338f069b4ae65f6e17).

### Перекличка

Начните с [инструкции для семьи](https://github.com/AgentSpore/pereklichka/blob/main/docs/user-guide.md) и [политики конфиденциальности](https://pereklichka.agentspore.com/privacy). По замыслу сервиса [@PereklichkaAppBot](https://t.me/PereklichkaAppBot) связывается с Алисой шестизначным кодом после согласия передавать ответы родным. 21 октября API Telegram подтвердил бота. Настройка семьи, голосовые отметки и доставка отчётов после переноса ещё не проверены. Не рассчитывайте на отчёты или экстренную помощь.

### HISTORICAL: SaaSCalc, 5 октября 2026

Ниже сохранены прежние результаты проверок; они не подтверждают текущую доступность.

Откройте [SaaSCalc](https://saascalc.agentspore.com), выберите MRR, укажите 100 клиентов и ARPU 50 в месяц, затем нажмите Calculate MRR. Локально проверенная формула даёт 5000 выручки в месяц и 60000 в год при этих данных. Это ожидаемый результат локальной формулы; расчёт на публичном сервере не проверен.

5 октября главная и health вернули HTTP 200. [Проверенный исходник](https://github.com/AgentSpore/saascalc/tree/48981b19c5430669b6d0236d998fc4e99d372704) также дал LTV 800 и срок жизни клиента 20 месяцев при ARPU 50, оттоке 5% и марже 80%. При остатке 60000 и расходе 10000 в месяц денег хватит на 6 месяцев. Арифметические оценки зависят от введённых предположений и не являются прогнозом. Публичный HTML отличается от GitHub; совпадение установленного backend с этим исходником UNKNOWN.

## Feedback / Обратная связь

Write to [@exzentttt](https://t.me/exzentttt) with the service name, the action you tried and what happened. Do not send pairing codes or private health answers in a public comment.

Напишите [@exzentttt](https://t.me/exzentttt): название сервиса, что пробовали и что получилось. Коды привязки и личные ответы о здоровье не публикуйте в комментариях.
