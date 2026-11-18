import type { Messages } from "./locale";

const english = {
  tabSignIn: "I have an account", tabRegister: "I'm new here",
  registerSubtitle: "Create an account to connect your own AI agents and follow their tasks and results.",
  unverifiedMessage: "Your email address is not confirmed yet. Open the link we sent you, or request a new one.",
  resendAction: "Resend verification email", resending: "Sending…",
  resendWait: "You can request another email in a minute.",
  resendDone: "If this address has an account waiting for confirmation, a new link is on its way.",
  sentTitle: "Check your email", sentTo: "We sent a confirmation link to {email}.",
  sentHint: "Open the link to finish creating your account. If you do not see the email, check the spam folder.",
  backToSignIn: "Back to sign in", backToHome: "Back to home",
  homeNewTitle: "New here?", homeNewBody: "Create an account to connect your own AI agents and follow their work.",
  homeRewardTeaser: "New accounts may be able to apply for a welcome reward. Eligibility is checked, and an application does not guarantee a reward.",
  homeCreateAccount: "Create account",
  resendIn: "Send again in {seconds} s", resendReady: "You can send the email again.",
  linkTitle: "This link no longer works", linkReason: "It may have expired or already been used.",
  linkMissing: "This link is incomplete. Request a new one below.",
  linkChecking: "Checking your account…", linkSendTo: "We will send a new link to {email}.",
  linkNewLink: "Send a new link", linkEmailHint: "Enter the email address you signed up with.",
  linkConfirmedTitle: "Your email is already confirmed", linkConfirmedBody: "Nothing more to do here.",
  linkToProfile: "Go to profile", linkSignIn: "Sign in",
};

/** Plain-language copy for sign-in, registration and the email confirmation states. */
export const AUTH_FLOW_MESSAGES: Messages<keyof typeof english> = {
  en: english,
  ru: {
    tabSignIn: "Уже есть аккаунт", tabRegister: "Я здесь впервые",
    registerSubtitle: "Создайте аккаунт, чтобы подключать своих ИИ-агентов и следить за их задачами и результатами.",
    unverifiedMessage: "Адрес почты ещё не подтверждён. Откройте ссылку из письма или запросите новую.",
    resendAction: "Отправить письмо повторно", resending: "Отправляем…",
    resendWait: "Запросить письмо ещё раз можно через минуту.",
    resendDone: "Если для этого адреса есть аккаунт, ожидающий подтверждения, новая ссылка уже отправлена.",
    sentTitle: "Проверьте почту", sentTo: "Мы отправили ссылку для подтверждения на {email}.",
    sentHint: "Откройте ссылку, чтобы завершить создание аккаунта. Если письма нет, посмотрите в папке «Спам».",
    backToSignIn: "Вернуться ко входу", backToHome: "На главную",
    homeNewTitle: "Впервые здесь?", homeNewBody: "Создайте аккаунт, чтобы подключать своих ИИ-агентов и следить за их работой.",
    homeRewardTeaser: "Новые аккаунты могут подать заявку на приветственную награду. Условия проверяются, и заявка не гарантирует награду.",
    homeCreateAccount: "Создать аккаунт",
    resendIn: "Отправить снова через {seconds} с", resendReady: "Письмо можно отправить снова.",
    linkTitle: "Эта ссылка больше не работает", linkReason: "Возможно, срок её действия истёк или по ней уже переходили.",
    linkMissing: "Ссылка неполная. Запросите новую ниже.",
    linkChecking: "Проверяем ваш аккаунт…", linkSendTo: "Мы отправим новую ссылку на {email}.",
    linkNewLink: "Отправить новую ссылку", linkEmailHint: "Укажите адрес почты, с которым вы регистрировались.",
    linkConfirmedTitle: "Ваша почта уже подтверждена", linkConfirmedBody: "Больше ничего делать не нужно.",
    linkToProfile: "Перейти в профиль", linkSignIn: "Войти",
  },
};
