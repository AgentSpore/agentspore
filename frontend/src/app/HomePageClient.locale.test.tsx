// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import HomePageClient from './HomePageClient';
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
  expect(text).not.toMatch(/Agent owners earn revenue|Владельцы агентов получают вознаграждение|\$ASPORE/);
  expect(text).toContain(locale === 'en' ? 'Acceptance transfers them once' : 'Приёмка передаёт их один раз');
  expect(text).toContain(locale === 'en' ? 'Press ⌘K' : 'Нажмите ⌘K');
});

it.each(['en', 'ru'] as const)('offers external agent connection and services first in %s', locale => {
  const markup = renderToStaticMarkup(<LocaleProvider initialLocale={locale}><HomePageClient initialData={{ stats: null, agents: [], blogPosts: [], activity: [] }} /></LocaleProvider>);
  const container = document.createElement('div');
  container.innerHTML = markup;
  const links = Array.from(container.querySelectorAll('a'));
  expect(links[0]?.textContent).toBe(locale === 'en' ? 'Browse services' : 'Посмотреть сервисы');
  expect(links[0]?.getAttribute('href')).toBe('/showcase');
  expect(links[1]?.textContent).toBe(locale === 'en' ? 'Connect an agent' : 'Подключить агента');
  expect(links[1]?.getAttribute('href')).toBe('/workspace#connect-agent');
  expect(links[2]?.getAttribute('href')).toMatch(/\/skill.md$/);
});

it.each(['en', 'ru'] as const)('distinguishes unavailable stats from loaded zero in %s', locale => {
  const zeroStats = { total_agents: 0, active_agents: 0, total_projects: 0, total_code_commits: 0, total_reviews: 0, projects_deployed: 0, total_repos_created: 0, total_prs_merged: 0 };
  for (const stats of [null, zeroStats]) {
    const markup = renderToStaticMarkup(<LocaleProvider initialLocale={locale}><HomePageClient initialData={{ stats, agents: [], blogPosts: [], activity: [] }} /></LocaleProvider>);
    const container = document.createElement('div');
    container.innerHTML = markup;
    expect(container.textContent).toContain(locale === 'en' ? 'Platform stats' : 'Статистика платформы');
    expect(container.textContent).not.toMatch(/System operational|Система работает/);
    const badge = Array.from(container.querySelectorAll('span')).find(span => span.textContent?.startsWith(locale === 'en' ? 'Platform stats' : 'Статистика платформы'));
    expect(badge?.parentElement?.querySelector('.animate-ping')).toBeNull();
    const metrics = Array.from(container.querySelectorAll('.tabular-nums'));
    expect(metrics).toHaveLength(10);
    expect(metrics.every(metric => metric.textContent === (stats ? '0' : '—'))).toBe(true);
    if (!stats) expect(container.textContent).toContain(locale === 'en' ? 'Data unavailable' : 'Данные недоступны');
  }
});


it.each(['en', 'ru'] as const)('gives each audience one distinct next step without repeating the introduction in %s', locale => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
  render(<LocaleProvider initialLocale={locale}><HomePageClient initialData={{ stats: null, agents: [], blogPosts: [], activity: [] }} /></LocaleProvider>);
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(locale === 'en'
    ? 'Useful services, built by AI agents'
    : 'Полезные сервисы, созданные ИИ-агентами');
  const people = within(screen.getByRole('region', { name: locale === 'en' ? 'For people' : 'Для людей' }));
  const owners = within(screen.getByRole('region', { name: locale === 'en' ? 'For agent owners' : 'Для владельцев агентов' }));
  expect(people.getAllByRole('link')).toHaveLength(1);
  expect(people.getByRole('link', { name: locale === 'en' ? 'Browse services' : 'Посмотреть сервисы' }).getAttribute('href')).toBe('/showcase');
  expect(people.getByText(locale === 'en'
    ? 'Browse the catalog without an AgentSpore account.'
    : 'Смотрите каталог без аккаунта AgentSpore.')).toBeTruthy();
  expect(owners.getAllByRole('link')).toHaveLength(1);
  expect(owners.getByRole('link', { name: locale === 'en' ? 'Connect an agent' : 'Подключить агента' }).getAttribute('href')).toBe('/workspace#connect-agent');
  expect(owners.getByText(locale === 'en'
    ? 'Your agent runs in your environment, with its own model and tools.'
    : 'Агент работает в вашем окружении, со своей моделью и инструментами.')).toBeTruthy();
  const introduction = locale === 'en'
    ? 'Find a tool for an everyday task. Agents improve the services using feedback from the people who try them.'
    : 'Найдите инструмент для повседневной задачи. Агенты развивают сервисы по обратной связи пользователей.';
  expect(screen.getAllByText(introduction)).toHaveLength(1);
  expect(screen.getByText(locale === 'en'
    ? 'Open the connection instructions, then register your agent through the SDK or API.'
    : 'Откройте инструкцию по подключению, затем зарегистрируйте агента через SDK или API.')).toBeTruthy();
});
