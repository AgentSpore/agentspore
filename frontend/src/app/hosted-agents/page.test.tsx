// @vitest-environment jsdom
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import Page from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => null }));
vi.mock('@/components/FreshnessBadge', () => ({ FreshnessBadge: () => null }));
vi.mock('@/hooks/usePolledResource', () => ({ usePolledResource: () => ({ data: { hostedAgents: [], externalAgents: [] }, loading: false, error: null }) }));
it.each(['en', 'ru'] as const)('explains the first task, conversation and result in %s', locale => {
  const container = document.createElement('div');
  container.innerHTML = renderToStaticMarkup(<LocaleProvider initialLocale={locale}><Page /></LocaleProvider>);
  expect(container.textContent).toContain(locale === 'en' ? 'Choose a task' : 'Выберите задачу');
  expect(container.textContent).toContain(locale === 'en' ? 'Start a conversation' : 'Начните разговор');
  expect(container.textContent).toContain(locale === 'en' ? 'Check the result' : 'Проверьте результат');
  expect(container.querySelector('details')?.open).toBe(false);
  expect(container.querySelector('a[href="/hosted-agents/new"]')).not.toBeNull();
});
