// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import TeamPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { LanguageSelector } from '@/components/LanguageSelector';
import { publicMessages } from '@/lib/i18n/public';

const auth = vi.hoisted(() => ({ fetchWithAuth: vi.fn() }));
vi.mock('@/lib/auth', () => auth);
vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'synthetic-team' }), useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Header', () => ({ Header: () => <LanguageSelector /> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); auth.fetchWithAuth.mockReset(); });

it('translates a persisted failed send without resubmitting or changing message text', async () => {
  vi.stubGlobal('EventSource', class { close() {} });
  Object.defineProperty(Element.prototype, 'scrollIntoView', { value: vi.fn(), configurable: true });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes('/messages?') ? [] : {
    id: 'synthetic-team', name: 'Synthetic Team', description: '', creator_name: 'Synthetic Owner', members: [], projects: [], created_at: '2026-01-01T00:00:00Z',
  } })));
  auth.fetchWithAuth.mockImplementation(async (_url: string, options?: RequestInit) => {
    if (options?.method === 'POST') throw new TypeError('Synthetic network failure');
    return { ok: true, json: async () => ({ name: 'Synthetic User' }) };
  });
  render(<LocaleProvider initialLocale="en"><TeamPage /></LocaleProvider>);
  await waitFor(() => expect(screen.getByRole('heading', { name: 'Synthetic Team' })).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }));
  const message = screen.getByRole('textbox');
  fireEvent.change(message, { target: { value: 'Online' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await waitFor(() => expect(screen.getByText(publicMessages.en.networkError)).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
  expect(screen.getByText(publicMessages.ru.networkError)).toBeTruthy();
  expect((message as HTMLTextAreaElement).value).toBe('Online');
  expect(auth.fetchWithAuth.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
});
