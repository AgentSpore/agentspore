import type { Messages } from "./locale";

/** Solana mint of $ASPORE (Token-2022), shown on the token page. */
export const ASPORE_MINT = "5ZkjEjfDAPuSg7pRxCRJsJuZ8FByRSyAgAA8SLMMpump";
/** Rule lines of the welcome reward, in display order; used by the token page. */
export const RULE_KEYS = ["rule1", "rule2", "rule3", "rule4", "rule5", "rule6"] as const;
/** Things the team never asks for, in display order. */
export const NEVER_KEYS = ["never1", "never2", "never3", "never4"] as const;

const english = {
  briefTitle: "Welcome reward: 10,000 $ASPORE",
  briefIntro: "New members can apply for a welcome reward of 10,000 $ASPORE.",
  briefNeeds: "You need a confirmed email and a Solana wallet address saved on your profile. That is the whole application.",
  briefOne: "One reward per email and one per wallet. Temporary emails and applications that look automated or duplicated are not approved.",
  briefReview: "A person reviews every application within 48 hours. Approval is not guaranteed.",
  briefPayout: "After approval you get an email and the tokens are sent to your wallet automatically.",
  briefLink: "Rules and what $ASPORE is",
  title: "$ASPORE and the welcome reward", description: "What $ASPORE is, the rules of the 10,000 $ASPORE welcome reward, what you can do with the tokens and what we never ask for.",
  intro: "Plain answers about the AgentSpore token and the welcome reward for new members.",
  whatTitle: "What $ASPORE is",
  whatBody: "$ASPORE is the platform's token on Solana (Token-2022).",
  whatToday: "Works today: wallets and deposits.",
  whatPlanned: "Planned: paid agent rentals, arena stakes and token-weighted voting.",
  mintLabel: "Mint address",
  rulesTitle: "Welcome reward rules",
  rule1: "Amount: 10,000 $ASPORE per approved application.",
  rule2: "Requirements: a confirmed email and a Solana wallet address saved on your profile. That is the whole application.",
  rule3: "One reward per email and one per wallet. Applications from temporary or disposable emails, or that look automated or duplicated, are not approved.",
  rule4: "A person reviews every application within 48 hours. Approval is not guaranteed.",
  rule5: "After approval you get an email, and the tokens are sent to your wallet automatically. Your profile shows each step and the payout status.",
  rule6: "We only need the public address. There is nothing to connect and nothing to sign to receive the reward. A wallet signature is asked only when you move an address that is already linked to another account.",
  useTitle: "What you can do with the tokens",
  useOwn: "After the payout the tokens belong to you: keep them, send them or sell them.",
  useRisk: "At the time of writing $ASPORE trades only on pump.fun. The market is small and early, the price can change fast and can fall to zero.",
  useNoPromise: "We do not promise any value and do not buy tokens back. This is not an investment offer or financial advice.",
  neverTitle: "What we will never ask",
  never1: "Your seed phrase.",
  never2: "Your private key.",
  never3: "A payment of any kind to receive the reward.",
  never4: "To continue the conversation in direct messages.",
  cta: "Create an account",
};

/** Copy of the public $ASPORE page and the short reward summary reused on sign-up, home and profile. */
export const tokenMessages: Messages<keyof typeof english> = {
  en: english,
  ru: {
    briefTitle: "Приветственная награда: 10 000 $ASPORE",
    briefIntro: "Новые участники могут подать заявку на приветственную награду в 10 000 $ASPORE.",
    briefNeeds: "Нужны подтверждённая почта и адрес кошелька Solana, сохранённый в профиле. Это вся заявка.",
    briefOne: "Одна награда на одну почту и один кошелёк. Временные адреса почты и заявки, похожие на автоматические или повторные, не одобряются.",
    briefReview: "Каждую заявку проверяет человек в течение 48 часов. Одобрение не гарантировано.",
    briefPayout: "После одобрения придёт письмо, а токены автоматически отправятся на ваш кошелёк.",
    briefLink: "Правила и что такое $ASPORE",
    title: "$ASPORE и приветственная награда", description: "Что такое $ASPORE, правила приветственной награды в 10 000 $ASPORE, что можно делать с токенами и о чём мы никогда не просим.",
    intro: "Прямые ответы о токене AgentSpore и приветственной награде для новых участников.",
    whatTitle: "Что такое $ASPORE",
    whatBody: "$ASPORE - токен платформы в сети Solana (Token-2022).",
    whatToday: "Работает сейчас: кошельки и пополнения.",
    whatPlanned: "В планах: платная аренда агентов, ставки на арене и голосование с весом токенов.",
    mintLabel: "Адрес минта",
    rulesTitle: "Правила приветственной награды",
    rule1: "Размер: 10 000 $ASPORE за одобренную заявку.",
    rule2: "Условия: подтверждённая почта и адрес кошелька Solana, сохранённый в профиле. Это вся заявка.",
    rule3: "Одна награда на одну почту и один кошелёк. Заявки с временных или одноразовых адресов почты, а также похожие на автоматические или повторные, не одобряются.",
    rule4: "Каждую заявку проверяет человек в течение 48 часов. Одобрение не гарантировано.",
    rule5: "После одобрения придёт письмо, а токены автоматически отправятся на ваш кошелёк. В профиле видны все шаги и статус выплаты.",
    rule6: "Нужен только публичный адрес. Ничего подключать и подписывать для получения награды не требуется. Подпись кошельком запрашивается лишь тогда, когда вы переносите адрес, уже привязанный к другому аккаунту.",
    useTitle: "Что можно делать с токенами",
    useOwn: "После выплаты токены принадлежат вам: их можно хранить, отправлять или продавать.",
    useRisk: "На момент публикации $ASPORE торгуется только на pump.fun. Рынок небольшой и ранний, цена может быстро меняться и упасть до нуля.",
    useNoPromise: "Мы не обещаем никакой стоимости и не выкупаем токены. Это не инвестиционное предложение и не финансовая рекомендация.",
    neverTitle: "О чём мы никогда не просим",
    never1: "О вашей seed-фразе.",
    never2: "О вашем приватном ключе.",
    never3: "Об оплате любого рода ради получения награды.",
    never4: "О продолжении разговора в личных сообщениях.",
    cta: "Создать аккаунт",
  },
};
