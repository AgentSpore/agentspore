// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import HomePageClient from './HomePageClient';
import { AUTH_FLOW_MESSAGES } from '@/lib/i18n/authFlow';
import { tokenMessages } from '@/lib/i18n/tokenPage';
import { docsMessages } from '@/lib/i18n/docs';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';

vi.mock('next/navigation', () => ({ usePathname: () => '/', useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(['en', 'ru'] as const)('describes educational accounting without promising earnings and keeps shortcuts spaced in %s', locale => {
  const markup = renderToStaticMarkup(<LocaleProvider initialLocale={locale}><HomePageClient initialData={{ stats: null, agents: [], blogPosts: [], activity: [] }} /></LocaleProvider>);
  const container = document.createElement('div');
  container.innerHTML = markup;
  const text = container.textContent!;
  expect(text).toContain(locale === 'en' ? 'Educational units, not money' : 'Учебные единицы, не деньги');
  expect(text.replace(AUTH_FLOW_MESSAGES[locale].homeRewardTeaser, '').replace(tokenMessages[locale].briefLink, '').replace(docsMessages[locale].fToken, '')).not.toMatch(/Agent owners earn revenue|Владельцы агентов получают вознаграждение|\$ASPORE/);
  expect(text).toContain(locale === 'en' ? 'Acceptance transfers them once' : 'Приёмка передаёт их один раз');
  expect(text).toContain(locale === 'en' ? 'Press ⌘K' : 'Нажмите ⌘K');
});

it.each(['en', 'ru'] as const)('offers one service action and an agent link before registration in %s', locale => {
  const markup = renderToStaticMarkup(<LocaleProvider initialLocale={locale}><HomePageClient initialData={{ stats: null, agents: [], blogPosts: [], activity: [] }} /></LocaleProvider>);
  const container = document.createElement('div');
  container.innerHTML = markup;
  const links = Array.from(container.querySelectorAll('a'));
  expect(links[0]?.textContent).toBe(locale === 'en' ? 'Try a service' : 'Попробовать сервис');
  expect(links[0]?.getAttribute('href')).toBe('#home-services-title');
  expect(links[1]?.textContent).toBe(locale === 'en' ? 'Connect an agent' : 'Подключить агента');
  expect(links[1]?.getAttribute('href')).toBe('/workspace#connect-agent');
  const hrefs = links.map(link => link.getAttribute('href'));
  expect(hrefs.indexOf('/login?tab=register')).toBeGreaterThan(hrefs.indexOf('/showcase'));
  expect(hrefs.indexOf('/token')).toBeLessThan(hrefs.indexOf('/login?tab=register'));
});

it.each(['en', 'ru'] as const)('distinguishes unavailable stats from loaded zero in one quiet line in %s', locale => {
  const zeroStats = { total_agents: 0, active_agents: 0, total_projects: 0, total_code_commits: 0, total_reviews: 0, projects_deployed: 0, total_repos_created: 0, total_prs_merged: 0 };
  for (const stats of [null, zeroStats]) {
    const markup = renderToStaticMarkup(<LocaleProvider initialLocale={locale}><HomePageClient initialData={{ stats, agents: [], blogPosts: [], activity: [] }} /></LocaleProvider>);
    const container = document.createElement('div');
    container.innerHTML = markup;
    expect(container.textContent).not.toMatch(/System operational|Система работает/);
    const lines = Array.from(container.querySelectorAll('p.tabular-nums'));
    expect(lines).toHaveLength(1);
    if (stats) expect(lines[0]?.textContent).toMatch(/^0 .+(· 0 .+){5}$/);
    else expect(lines[0]?.textContent).toBe(locale === 'en' ? 'Data unavailable' : 'Данные недоступны');
    expect(lines[0]?.textContent).not.toContain('\u2014');
  }
});

it.each(['en', 'ru'] as const)('keeps the headline and one-sentence introduction without the audience cards in %s', locale => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
  render(<LocaleProvider initialLocale={locale}><HomePageClient initialData={{ stats: null, agents: [], blogPosts: [], activity: [] }} /></LocaleProvider>);
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(locale === 'en'
    ? 'Useful services, built by AI agents'
    : 'Полезные сервисы, созданные ИИ‑агентами');
  expect(screen.queryByRole('region', { name: locale === 'en' ? 'For people' : 'Для людей' })).toBeNull();
  expect(screen.queryByRole('region', { name: locale === 'en' ? 'For agent owners' : 'Для владельцев агентов' })).toBeNull();
  const introduction = locale === 'en'
    ? 'Find a tool for an everyday task, built and improved by AI agents from the feedback of people who use it.'
    : 'Найдите инструмент для повседневной задачи: агенты создают и улучшают сервисы по отзывам тех, кто ими пользуется.';
  expect(screen.getAllByText(introduction)).toHaveLength(1);
  expect(within(screen.getByRole('main')).getByText(locale === 'en'
    ? 'Open the connection instructions, then register your agent through the SDK or API.'
    : 'Откройте инструкцию по подключению, затем зарегистрируйте агента через SDK или API.')).toBeTruthy();
});
