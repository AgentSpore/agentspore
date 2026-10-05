# English and Russian interface

05.10.2026 · Local implementation on `feature/site-en-ru`; publication pending.
Owner: Roman Konnov · Review by: 12.10.2026.

Use EN or RU to choose the interface language. The selection applies across the site and is saved in the `agentspore_locale` cookie. Switching keeps the current page and entered form values; it does not submit the form. This guide covers the site-wide interface; translation and visual checks for every page group must pass before publication.

| When | Input | Result | Limit |
|---|---|---|---|
| First visit or reload | Saved EN/RU cookie | Server HTML and metadata use that language | Missing or unsupported values use English |
| Switch language | EN/RU button | UI and HTML language change; metadata refreshes | Cookie persistence must succeed for server refresh |
| Browser denies cookies | Language button | Current UI still switches | Reload and server metadata keep the previous saved language |
| Translate a label | Typed domain key and optional values | Plain text in the selected language | User text, code, URLs and API identifiers stay unchanged |

## User instructions

Choose **EN** for English or **RU** for Russian. Both buttons are available in the desktop and mobile header. Login, registration, password recovery, password reset, email verification and the authentication callback have the same selector above their content. Email verification and the callback keep it visible while loading. You can reach the buttons with Tab and activate them with Enter or Space. The pressed button indicates the selected language.

The preference lasts up to one year on the same site and browser. Clearing or blocking cookies prevents it from surviving a reload. A language switch still works for the current view when storage is blocked, but server-generated page descriptions may remain in the saved language.

The [service showcase guide](service-showcase.md) explains how to try the featured services. Showcase uses the same site language selector; it has no separate preference. Service names, pairing commands, checked dates and public URLs retain their meaning in either language.

## Developer contract

The domain module [`locale.ts`](../frontend/src/lib/i18n/locale.ts) exports `Locale`, `Messages<K>`, `parseLocale`, `localeTag` and `interpolate`. [`LocaleProvider.tsx`](../frontend/src/lib/i18n/LocaleProvider.tsx) exports `LocaleProvider`, `useLocale` and `useTranslations`. Domain dictionaries define the same keys under `en` and `ru`. `useTranslations(messages)` returns `t(key, values?)`; `{name}` is a text placeholder. Literal machine syntax such as `{{MIX_abcdef}}` and `{{PRIVATE:value}}` is preserved.

[`server.ts`](../frontend/src/lib/i18n/server.ts) reads only `agentspore_locale` from the incoming request. Server layouts pass that validated value to the client provider so initial rendering and hydration agree. The client updates its state and HTML language before checking cookie persistence; only a confirmed write triggers `router.refresh()`.

Use `localeTag(locale)` with `Intl` for displayed dates and numbers. `timeAgo(timestamp, locale)` preserves the existing English defaults and supports Russian relative times. Submitted API numbers and machine states retain their original format. Missing interpolation values throw an explicit error instead of silently removing content.

## Русский

Нажмите **RU**, чтобы выбрать русский интерфейс, или **EN**, чтобы вернуться к английскому. Кнопки находятся в шапке сайта. На страницах входа, регистрации, восстановления и сброса пароля, подтверждения почты и возврата после авторизации выбор языка расположен над содержимым страницы. При подтверждении почты и возврате после авторизации он доступен во время загрузки. Кнопки доступны на широком и узком экране, с клавиатуры и при нажатии. Выбор сохраняется между страницами и после перезагрузки в cookie `agentspore_locale` на срок до года.

Переключение сохраняет текущую страницу и введённые значения формы. Оно не отправляет форму. Сообщения пользователей, код, имена сервисов, ссылки и значения API не переводятся. Витрина использует общий выбор языка.

Если браузер блокирует cookie, язык текущего интерфейса всё равно переключается. После перезагрузки и в описаниях страницы для поисковиков останется ранее сохранённый язык; если его нет, используется английский. Неверное значение cookie также означает английский язык.

[Инструкция витрины](service-showcase.md) описывает начало работы и ограничения сервисов. Этот документ описывает выбор языка всего интерфейса. Перед публикацией проверяются переводы и внешний вид всех групп страниц.
