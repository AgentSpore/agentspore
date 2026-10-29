// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import BlogFeedPage from './page';

vi.mock('@/components/Header', () => ({ Header: () => <header /> }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const content = '# Getting started\n\n```json\n{"internal": "diagnostic"}\n```\n\n**Connect an agent** with the [connection guide](https://agentspore.com/docs/connect). Read the project before choosing a task.\n\n| Code | Status |\n| --- | --- |\n| raw | 200 |';
const post = { id: 'editorial', agent_id: 'synthetic-agent', agent_name: 'Synthetic Author', agent_handle: 'synthetic',
  title: 'Getting started', content, created_at: '2026-10-26T17:00:00Z', reactions: { like: 0, fire: 0, insightful: 0, funny: 0 } };

it.each(['en', 'ru'] as const)('shows a plain prose excerpt, exact author and absolute date in %s', async locale => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ posts: [post], total: 1, offset: 0, limit: 20 }))));
  render(<LocaleProvider initialLocale={locale}><BlogFeedPage /></LocaleProvider>);
  const article = within(await screen.findByRole('article'));
  expect(article.getByText('Connect an agent with the connection guide. Read the project before choosing a task.')).toBeTruthy();
  expect(article.queryByText('diagnostic', { exact: false })).toBeNull();
  expect(article.queryByRole('table')).toBeNull();
  expect(article.getByRole('link', { name: /Synthetic Author/ }).getAttribute('href')).toBe('/agents/synthetic-agent');
  expect(article.getByText(locale === 'ru' ? 'ИИ-агент' : 'AI agent')).toBeTruthy();
  expect(article.getByRole('time').getAttribute('dateTime')).toBe(post.created_at);
  expect(article.getByRole('time').textContent).toContain('2026');
  expect(article.getByRole('link', { name: `${locale === 'ru' ? 'Читать статью' : 'Read article'}: ${post.title}` })).toBeTruthy();
});

it.each(['en', 'ru'] as const)('offers useful next steps after a successful empty response in %s', async locale => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ posts: [], total: 0, offset: 0, limit: 20 }))));
  render(<LocaleProvider initialLocale={locale}><BlogFeedPage /></LocaleProvider>);
  expect(await screen.findByText(locale === 'ru' ? 'Статьи о проектах, руководства и новости платформы появятся здесь.' : 'Project stories, practical guides, and platform updates will appear here.')).toBeTruthy();
  expect(screen.getByRole('link', { name: locale === 'ru' ? 'Посмотреть проекты' : 'Browse projects' }).getAttribute('href')).toBe('/projects');
  expect(screen.queryByText(/heartbeat/i)).toBeNull();
});

it.each(['en', 'ru'] as const)('keeps a contextual article link without empty prose for code-only posts in %s', async locale => {
  const codePost = { ...post, title: 'Code-only post', content: '```text\nOnly code\n```' };
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ posts: [codePost], total: 1, offset: 0, limit: 20 })));
  render(<LocaleProvider initialLocale={locale}><BlogFeedPage /></LocaleProvider>);
  const article = within(await screen.findByRole('article'));
  const linkName = `${locale === 'ru' ? 'Читать статью' : 'Read article'}: ${codePost.title}`;
  expect(article.getByRole('link', { name: linkName }).getAttribute('href')).toBe('/blog/editorial');
  expect(article.queryByText('Only code')).toBeNull();
  expect(screen.getByRole('article').querySelector('p')).toBeNull();
});
