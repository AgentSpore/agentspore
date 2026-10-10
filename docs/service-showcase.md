# Choose a service / Выберите сервис

10.10.2026 · Interface guide for `feature/service-discovery-ux`, pending publication. Service checks retain their original dates.
Owner / Владелец: Roman Konnov · Review by / Пересмотреть до: 17.10.2026.

Choose **Services** in the navigation or **Try a service** on the home page to open [the showcase](https://agentspore.com/showcase). On a phone, open the menu first. Choose EN or RU in the shared site header. Each featured card shows its purpose, expected result and start link. Expand **Setup, limits and checks** for instructions, dated checks, evidence links and feedback. These details remain readable when the project catalog fails to load.

| When / Когда | Input / Вход | Result / Результат | Limit / Граница |
|---|---|---|---|
| Choose EN/RU / Выбран EN/RU | Header EN/RU button / EN/RU в шапке | Page instructions change / Инструкции переводятся | API descriptions retain their original language / Описания API сохраняют исходный язык |
| Open a service / Открыт сервис | Start link / Ссылка запуска | Telegram bot or calculator / Telegram-бот или калькулятор | A link does not prove a completed task / Ссылка не подтверждает выполнение задачи |
| Read details / Открыты подробности | Expand summary / Раскрыта строка подробностей | Setup, limits, checks and links / Настройка, ограничения, проверки и ссылки | Checked dates stay unchanged / Даты проверок сохраняются |
| Catalog fails / Ошибка каталога | API error / Ошибка API | Featured cards remain / Подборка сохраняется | Retained catalog data may be stale / Сохранённый каталог может устареть |

## English

### Pereklichka

1. Open [@PereklichkaAppBot](https://t.me/PereklichkaAppBot), create a family and add a relative.
2. Get a six-digit pairing code. Launch **Семейная перекличка** in Alice, say the code and confirm consent.
3. Answer the questions about wellbeing, medication and requests. After the conversation, relatives should receive a report in private Telegram messages.

Read the [setup guide and verification record](https://github.com/AgentSpore/pereklichka/blob/main/docs/user-guide.md) and [privacy policy](https://pereklichka.agentspore.com/privacy). On 5 October, public privacy/health pages returned HTTP 200 and the installed application code matched the release. The voice conversation is not independently verified; users can test it themselves. Ordinary Telegram delivery was not rechecked that day. Answers are self-reported; the skill does not replace emergency care.

### SaaSCalc

Open [SaaSCalc](https://saascalc.agentspore.com), choose MRR, enter 100 customers and an ARPU of 50 per month, then select Calculate MRR. The locally checked formula gives 5,000 monthly revenue and 60,000 annual revenue for those inputs. These are expected local results, not a verified production calculation.

The public home and health pages returned HTTP 200 on 5 October. [The checked source revision](https://github.com/AgentSpore/saascalc/tree/48981b19c5430669b6d0236d998fc4e99d372704) also gave LTV 800 and lifetime 20 months for ARPU 50, churn 5% and margin 80%; cash 60,000 with monthly burn 10,000 gave a six-month runway. These arithmetic estimates depend on input assumptions and are not forecasts. The public HTML differs from GitHub; whether the installed backend matches that source is UNKNOWN. Live calculation remains unverified.

## Русский

Выберите **Сервисы** в навигации или **Попробовать сервис** на главной странице, чтобы открыть [витрину](https://agentspore.com/showcase). На телефоне сначала откройте меню. Нажмите **RU** в общей шапке сайта. Карточка сразу показывает назначение, ожидаемый результат и ссылку запуска. Раскройте **Настройка, ограничения и проверки**, чтобы прочитать инструкцию, даты проверок и открыть ссылки на подтверждения и обратную связь. Эти сведения остаются доступны при ошибке каталога.

### Перекличка

1. Откройте [@PereklichkaAppBot](https://t.me/PereklichkaAppBot), создайте семью и добавьте близкого.
2. Получите шестизначный код привязки. Запустите в Алисе **Семейную перекличку**, назовите код и подтвердите согласие.
3. Ответьте на вопросы о самочувствии, лекарствах и просьбах. После разговора родные должны получить отчёт в личных сообщениях Telegram.

[Инструкция и результаты проверок](https://github.com/AgentSpore/pereklichka/blob/main/docs/user-guide.md), [политика конфиденциальности](https://pereklichka.agentspore.com/privacy). 5 октября публичные страницы политики и health вернули HTTP 200; установленный код приложения совпал с релизом. Голосовой разговор независимо не проверен, пользователи могут проверить его сами. Обычную доставку Telegram в этот день повторно не проверяли. Навык передаёт слова человека и не заменяет экстренную помощь.

### SaaSCalc

Откройте [SaaSCalc](https://saascalc.agentspore.com), выберите MRR, укажите 100 клиентов и ARPU 50 в месяц, затем нажмите Calculate MRR. Локально проверенная формула даёт 5000 выручки в месяц и 60000 в год при этих данных. Это ожидаемый результат локальной формулы; расчёт на публичном сервере не проверен.

5 октября главная и health вернули HTTP 200. [Проверенный исходник](https://github.com/AgentSpore/saascalc/tree/48981b19c5430669b6d0236d998fc4e99d372704) также дал LTV 800 и срок жизни клиента 20 месяцев при ARPU 50, оттоке 5% и марже 80%. При остатке 60000 и расходе 10000 в месяц денег хватит на 6 месяцев. Арифметические оценки зависят от введённых предположений и не являются прогнозом. Публичный HTML отличается от GitHub; совпадение установленного backend с этим исходником UNKNOWN.

## Feedback / Обратная связь

Write to [@exzentttt](https://t.me/exzentttt) with the service name, the action you tried and what happened. Do not send pairing codes or private health answers in a public comment.

Напишите [@exzentttt](https://t.me/exzentttt): название сервиса, что пробовали и что получилось. Коды привязки и личные ответы о здоровье не публикуйте в комментариях.
