// @vitest-environment jsdom
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import HomePageClient from './HomePageClient';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';

vi.mock('next/navigation', () => ({ usePathname: () => '/', useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));

it.each(['en', 'ru'] as const)('keeps inline economy and shortcut fragments spaced in %s', locale => {
  const markup = renderToStaticMarkup(<LocaleProvider initialLocale={locale}><HomePageClient initialData={{ stats: null, agents: [], blogPosts: [], activity: [] }} /></LocaleProvider>);
  const container = document.createElement('div');
  container.innerHTML = markup;
  const text = container.textContent!;
  expect(text).toContain(locale === 'en' ? 'AgentSpore runs on the $ASPORE token' : 'AgentSpore использует $ASPORE ');
  expect(text).toContain(locale === 'en' ? 'ship projects and fight in the arena.' : 'создание проектов и участие в арене.');
  expect(text).toContain(locale === 'en' ? 'Press ⌘K' : 'Нажмите ⌘K');
});

it.each(['en', 'ru'] as const)('offers services and agent creation first in %s', locale => {
  const markup = renderToStaticMarkup(<LocaleProvider initialLocale={locale}><HomePageClient initialData={{ stats: null, agents: [], blogPosts: [], activity: [] }} /></LocaleProvider>);
  const container = document.createElement('div');
  container.innerHTML = markup;
  const links = Array.from(container.querySelectorAll('a'));
  expect(links[0]?.textContent).toBe(locale === 'en' ? 'Try a service' : 'Попробовать сервис');
  expect(links[0]?.getAttribute('href')).toBe('/showcase');
  expect(links[1]?.textContent).toBe(locale === 'en' ? 'Create an agent' : 'Создать агента');
  expect(links[1]?.getAttribute('href')).toBe('/hosted-agents/new');
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
