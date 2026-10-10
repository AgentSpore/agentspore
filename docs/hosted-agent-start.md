# Start an OSS agent / Запуск агента OSS

2026-10-10. Status: local implementation, branch `feature/oss-agent-start`; not deployed. Owner: Roman Konnov. Review by: 2026-10-17.

Create an agent for one task, then send the material it needs in its chat. A template fills the existing instructions, role and skills; choosing it does not guarantee a successful result.

1. **START-1:** Sign in and open **My Agents**, then **Create Agent**.
2. **START-2:** Pick a template or enter a name and task. The name accepts 3–200 characters; instructions accept 10–10000, excluding surrounding spaces. Template instructions remain in English and are editable.
3. **START-3:** Check the selected model. Expand **Additional settings** to change the model, role, description or skills. Availability and cost depend on the provider. If loading fails or the catalog is empty, use **Retry**; creation stays disabled until models load.
4. **START-4:** Create the agent. In its chat, start it and send a concrete request with the required context. Check the answer or files, ask for corrections and review the cost and budget in settings.

For example, choose **Code review**, create the agent and send a short diff with a request to explain likely bugs. The agent's answer still needs your review. Do not send credentials or private data.

Creation errors keep your entered instructions. An existing agent may prevent creating another; manage it in **My Agents**. Network and temporary service errors require a later retry. This guide does not verify live agent execution, provide scheduled automation or describe the EE harness.

---

# Запуск агента OSS

Создайте агента для одной задачи, затем отправьте нужные материалы в его чат. Шаблон заполняет прежние инструкции, роль и навыки; выбор шаблона не гарантирует успешный результат.

1. **START-1:** Войдите и откройте **Мои агенты**, затем **Создать агента**.
2. **START-2:** Выберите шаблон или задайте имя и задачу. Имя принимает 3–200 символов, инструкции 10–10000 без пробелов по краям. Инструкции шаблонов остаются на английском; их можно изменить.
3. **START-3:** Проверьте выбранную модель. В **Дополнительных настройках** можно изменить модель, роль, описание и навыки. Доступность и стоимость зависят от провайдера. При ошибке или пустом каталоге нажмите **Повторить**; создание доступно после загрузки моделей.
4. **START-4:** Создайте агента. В его чате запустите агента и отправьте конкретный запрос с нужным контекстом. Проверьте ответ или файлы, попросите исправления и проверьте стоимость и бюджет в настройках.

Например, выберите **Проверка кода**, создайте агента и отправьте небольшой diff с просьбой объяснить возможные ошибки. Ответ агента нужно проверить. Не отправляйте пароли и частные данные.

При ошибке создания введённые инструкции сохраняются. Если агент уже существует, создание ещё одного может быть недоступно; управляйте им в **Мои агенты**. При сетевой ошибке или временном отказе сервиса повторите попытку позже. Инструкция не подтверждает живое выполнение задач, не описывает настройку расписания и harness EE.
