// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import CommandPalette from './CommandPalette';
import { LanguageSelector } from './LanguageSelector';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('translates result metadata while retaining content, query and loaded search results', async () => {
  const fetchMock = vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes('leaderboard') ? [
    { id: 'synthetic-agent', name: 'Sample Agent', handle: 'sample', specialization: 'reviewer', is_active: true },
  ] : url.includes('/projects?') ? [
    { id: 'synthetic-project', name: 'Sample Project', description: 'Online', status: 'building' },
    { id: 'synthetic-empty-project', name: 'Sample Empty', description: '', status: 'building' },
  ] : [{ id: 'synthetic-blog', title: 'Sample Post', agent_name: 'Sample Author', content: 'Online' }] }));
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale="en"><LanguageSelector /><CommandPalette /></LocaleProvider>);
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  const search = screen.getByRole('textbox');
  fireEvent.change(search, { target: { value: 'Sample' } });
  await waitFor(() => expect(screen.getByText('Sample Project')).toBeTruthy());
  expect(fetchMock).toHaveBeenCalledTimes(3);
  fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
  expect(screen.getByText('Online')).toBeTruthy();
  expect(screen.getByText('в разработке')).toBeTruthy();
  expect(screen.getByText('@sample · ревьюер')).toBeTruthy();
  expect(screen.getByText('автор Sample Author')).toBeTruthy();
  expect((search as HTMLInputElement).value).toBe('Sample');
  expect(fetchMock).toHaveBeenCalledTimes(3);
});
