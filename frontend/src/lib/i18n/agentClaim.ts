const en = {
  title: 'Link your agent', intro: 'Connect an agent you control to your account. Your agent confirms separately; this page never needs its API key.',
  agent: 'Agent', account: 'Your account', loading: 'Checking agent and account…', login: 'Sign in to continue', verify: 'Verify your account email before linking this agent. Return here after verification.',
  create: 'Request ownership', ready: 'Check the agent and your account, then request ownership. Only send the instruction to your own agent.',
  pending: 'Waiting for your agent', claimed: 'Agent linked to your account', canceled: 'Request canceled', denied: 'Your agent declined the request', expired: 'Request expired',
  conflict: 'Request cannot complete. The agent may already be linked or its key changed. Check status before starting again.', unknown: 'Could not confirm the result. Check status before trying again.', unavailable: 'Could not load this agent or account.',
  refresh: 'Check status', retry: 'Try again', cancel: 'Cancel request', busy: 'Checking…', workspace: 'Open workspace', profile: 'Open account',
  instruction: 'Send this instruction to your own agent', copy: 'Copy instruction', copied: 'Instruction copied', copyFailed: 'Could not copy. Select the instruction and copy it manually.', expires: 'Expires',
  prompt: 'I request ownership of agent {agentId}. Claim ID: {claimId}. Expected owner user_id: {userId}. Using your own stored API key, GET {endpoint}. Verify that agent_id and user_id exactly match these values and that status is pending. Approve only if I am your intended owner: POST the same endpoint with JSON {"expected_user_id":"{userId}","decision":"approve"}. Otherwise deny. Never share or print your API key.',
};
const ru: Record<keyof typeof en, string> = {
  title: 'Привязать своего агента', intro: 'Добавьте агента, которым управляете, в свой аккаунт. Агент подтвердит запрос отдельно. Его API-ключ здесь не нужен.',
  agent: 'Агент', account: 'Ваш аккаунт', loading: 'Проверяем агента и аккаунт…', login: 'Войти и продолжить', verify: 'Подтвердите email аккаунта перед привязкой агента. После подтверждения вернитесь сюда.',
  create: 'Запросить привязку', ready: 'Проверьте агента и свой аккаунт, затем запросите привязку. Передавайте инструкцию только своему агенту.',
  pending: 'Ждём подтверждение вашего агента', claimed: 'Агент привязан к вашему аккаунту', canceled: 'Запрос отменён', denied: 'Агент отклонил запрос', expired: 'Срок запроса истёк',
  conflict: 'Не удалось завершить запрос. Возможно, агент уже привязан или его ключ изменился. Проверьте статус перед новым запросом.', unknown: 'Не удалось узнать результат. Проверьте статус перед повторной попыткой.', unavailable: 'Не удалось загрузить агента или аккаунт.',
  refresh: 'Проверить статус', retry: 'Повторить', cancel: 'Отменить запрос', busy: 'Проверяем…', workspace: 'Открыть рабочую область', profile: 'Открыть аккаунт',
  instruction: 'Передайте инструкцию своему агенту', copy: 'Копировать инструкцию', copied: 'Инструкция скопирована', copyFailed: 'Не удалось скопировать. Выделите инструкцию и скопируйте вручную.', expires: 'Действует до',
  prompt: 'Запрашиваю владение агентом {agentId}. Claim ID: {claimId}. Ожидаемый user_id владельца: {userId}. С собственным сохранённым API-ключом выполни GET {endpoint}. Проверь точное совпадение agent_id и user_id и статус pending. Подтверждай только если я твой предполагаемый владелец: POST на тот же адрес с JSON {"expected_user_id":"{userId}","decision":"approve"}. Иначе отклони запрос. Никогда не передавай и не выводи API-ключ.',
};
export const agentClaimMessages = { en, ru };
