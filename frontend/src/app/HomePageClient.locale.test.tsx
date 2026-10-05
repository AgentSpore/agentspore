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
