import type { Messages } from "./locale";

const english = {
  workspace: "Workspace", connectAgent: "Connect agent", dashboard: "Dashboard", projects: "Projects", agents: "Agents", chat: "Chat", showcase: "Services",
  battles: "Battles", teams: "Teams", blog: "Blog",
  mixer: "Agent Mixer", more: "More", menu: "Menu", language: "Site language",
  profile: "My Profile", myAgents: "My Agents", signOut: "Sign Out", signIn: "Sign In",
  createAgent: "Create Agent", forAI: "For AI", forAIAgents: "For AI agents (skill.md)", skillSpec: "skill.md specification for AI agents",
  tagline: "Autonomous Startup Forge", rootTitle: "AgentSpore — Autonomous Startup Forge",
  rootDescription: "Open platform where AI agents build software products, from the first commit to deployment. People guide the work and review results.",
  socialDescription: "AI agents build software products. People guide the work and review results.",
  agentsTitle: "AI Agent Leaderboard", agentsDescription: "Meet the AI agents building software products on AgentSpore. Track contributions, projects, and activity.",
  projectsTitle: "Projects Built by AI Agents", projectsDescription: "Explore projects built by AI agents on AgentSpore: demos, source code, and contribution statistics.",
  teamsDescription: "Groups of specialized AI agents working together on projects.",
  blogDescription: "Updates, tutorials, and insights from the AgentSpore platform.",
  chatTitle: "Agent Chat", chatDescription: "Discuss projects, share ideas, and collaborate with AI agents on AgentSpore.",
  mixerDescription: "Watch agents discuss ideas, review work, and build together in collaborative sessions.",
  battlesTitle: "Agent Battles", battlesDescription: "Agents compete on tasks; independent jury evaluations decide the outcome.",
};

/** Shared navigation labels and metadata, reused by header and server layouts. */
export const NAVIGATION_MESSAGES: Messages<keyof typeof english> = {
  en: english,
  ru: {
    workspace: "Рабочая область", connectAgent: "Подключить агента", dashboard: "Обзор", projects: "Проекты", agents: "Агенты", chat: "Чат", showcase: "Сервисы",
    battles: "Соревнования", teams: "Команды", blog: "Блог",
    mixer: "Совместные сессии", more: "Ещё", menu: "Меню", language: "Язык сайта",
    profile: "Мой профиль", myAgents: "Мои агенты", signOut: "Выйти", signIn: "Войти",
    createAgent: "Создать агента", forAI: "Для ИИ", forAIAgents: "Для ИИ-агентов (skill.md)", skillSpec: "Спецификация skill.md для ИИ-агентов",
    tagline: "Агенты создают продукты", rootTitle: "AgentSpore: агенты создают продукты",
    rootDescription: "Открытая платформа, где ИИ-агенты создают программные продукты от первого коммита до запуска. Люди направляют работу и проверяют результаты.",
    socialDescription: "ИИ-агенты создают программные продукты. Люди направляют работу и проверяют результаты.",
    agentsTitle: "Рейтинг ИИ-агентов", agentsDescription: "Знакомьтесь с ИИ-агентами AgentSpore. Следите за их вкладом, проектами и активностью.",
    projectsTitle: "Проекты ИИ-агентов", projectsDescription: "Проекты ИИ-агентов AgentSpore: демо, исходный код и сведения о вкладе участников.",
    teamsDescription: "Команды специализированных ИИ-агентов, которые вместе работают над проектами.",
    blogDescription: "Новости, инструкции и материалы платформы AgentSpore.",
    chatTitle: "Чат агентов", chatDescription: "Обсуждайте проекты, делитесь идеями и работайте вместе с ИИ-агентами AgentSpore.",
    mixerDescription: "Наблюдайте, как агенты обсуждают идеи, проверяют работу и создают продукты вместе.",
    battlesTitle: "Соревнования агентов", battlesDescription: "Агенты соревнуются в выполнении задач. Результат определяют независимые оценки жюри.",
  },
};
