// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Page from './page';
import { useModelCatalog } from './useModelCatalog';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));
vi.mock('@/components/Header', () => ({ Header: () => null }));
beforeEach(() => { HTMLElement.prototype.scrollIntoView = vi.fn(); localStorage.setItem('access_token', 'synthetic-fixture'); vi.clearAllMocks(); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
it('shows model HTTP failure and blocks creation', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({}) }));
  render(<Page />);
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('models'));
  expect(screen.getByRole('button', { name: 'Create Agent' }).hasAttribute('disabled')).toBe(true);
});
it('rejects a two-character name before POST', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ models: [{ id: 'fixture/model', name: 'Fixture' }] }) });
  vi.stubGlobal('fetch', fetcher);
  const { container } = render(<Page />);
  await waitFor(() => expect(container.querySelector('textarea')).not.toBeNull());
  fireEvent.change(container.querySelector('input')!, { target: { value: 'ab' } });
  fireEvent.change(container.querySelector('textarea')!, { target: { value: 'Review supplied text carefully.' } });
  fireEvent.submit(container.querySelector('form')!);
  await act(async () => {});
  expect(fetcher.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
});
it.each(['en', 'ru'] as const)('translates the simple form and preserves template payload in %s', async locale => {
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ models: [{ id: 'fixture/model', name: 'Fixture', provider: 'new-provider' }] }) }).mockResolvedValueOnce({ ok: true, json: async () => ({ id: 'created' }) });
  vi.stubGlobal('fetch', fetcher);
  render(<LocaleProvider initialLocale={locale}><Page /></LocaleProvider>);
  await screen.findByText(locale === 'en' ? 'Selected model: Fixture' : 'Выбрана модель: Fixture');
  expect(screen.getByLabelText(locale === 'en' ? 'What should your agent do?' : 'Что должен делать агент?', { exact: false })).toBeTruthy();
  const advanced = screen.getByText(locale === 'en' ? 'Additional settings' : 'Дополнительные настройки').closest('details')!;
  expect(advanced.open).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: locale === 'en' ? /Code review/ : /Проверка кода/ }));
  fireEvent.submit(screen.getByLabelText(locale === 'en' ? 'Agent Name' : 'Имя агента').closest('form')!);
  await waitFor(() => expect(router.push).toHaveBeenCalledWith('/hosted-agents/created'));
  const payload = JSON.parse(fetcher.mock.calls[1][1].body);
  expect(payload).toMatchObject({ name: 'CodeReviewer', specialization: 'programmer', skills: ['code-review', 'security', 'static-analysis'], model: 'fixture/model' });
  expect(payload.system_prompt).toContain('You are a senior code reviewer.');
  expect(screen.getByRole('option', { name: 'Fixture' }).parentElement?.getAttribute('label')).toBe('new-provider');
});
it.each(['empty', 'network', 'malformed'] as const)('supports retry from %s catalog', async failure => {
  const fetcher = vi.fn();
  if (failure === 'network') fetcher.mockRejectedValueOnce(new Error('fixture failure'));
  else fetcher.mockResolvedValueOnce({ ok: true, json: async () => failure === 'empty' ? { models: [] } : { models: [{ id: 42 }] } });
  fetcher.mockResolvedValueOnce({ ok: true, json: async () => ({ models: [{ id: 'fixture/model', name: 'Fixture' }] }) });
  vi.stubGlobal('fetch', fetcher); render(<Page />);
  await screen.findByRole('alert'); fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByText('Selected model: Fixture');
  expect(screen.getByRole('button', { name: 'Create Agent' }).hasAttribute('disabled')).toBe(false);
});
it.each([409, 502, 'network'] as const)('keeps the task after create failure %s', async status => {
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ models: [{ id: 'fixture/model', name: 'Fixture' }] }) });
  if (status === 'network') fetcher.mockRejectedValueOnce(new Error('fixture failure'));
  else fetcher.mockResolvedValueOnce({ ok: false, status, json: async () => ({}) });
  vi.stubGlobal('fetch', fetcher); render(<Page />); await screen.findByText('Selected model: Fixture');
  fireEvent.change(screen.getByLabelText('Agent Name'), { target: { value: ' Agent ' } });
  fireEvent.change(screen.getByLabelText('What should your agent do?', { exact: false }), { target: { value: ' Review supplied text. ' } });
  fireEvent.submit(screen.getByLabelText('Agent Name').closest('form')!); await screen.findByRole('alert');
  expect(screen.getByLabelText('What should your agent do?', { exact: false })).toHaveProperty('value', ' Review supplied text. ');
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toMatchObject({ name: 'Agent', system_prompt: 'Review supplied text.' });
  expect(router.push).not.toHaveBeenCalled();
});
it('ignores a catalog response after unmount', async () => {
  let resolve!: (value: unknown) => void;
  const pending = new Promise(value => { resolve = value; });
  const fetcher = vi.fn().mockReturnValue(pending); vi.stubGlobal('fetch', fetcher);
  const page = render(<Page />); const signal: AbortSignal = fetcher.mock.calls[0][1].signal;
  page.unmount(); expect(signal.aborted).toBe(true);
  await act(async () => { resolve({ ok: true, json: async () => ({ models: [{ id: 'old/model', name: 'Old' }] }) }); });
  expect(router.push).not.toHaveBeenCalled();
});
it.each([['😀a', 'Review this text.'], ['Agent', '😀😀😀😀😀']])('rejects backend-short Unicode input %s', async (name, task) => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ models: [{ id: 'fixture/model', name: 'Fixture' }] }) });
  vi.stubGlobal('fetch', fetcher); render(<Page />); await screen.findByText('Selected model: Fixture');
  fireEvent.change(screen.getByLabelText('Agent Name'), { target: { value: name } });
  fireEvent.change(screen.getByLabelText('What should your agent do?', { exact: false }), { target: { value: task } });
  fireEvent.submit(screen.getByLabelText('Agent Name').closest('form')!); await act(async () => {});
  expect(fetcher.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
});
it.each([[2, 10, false], [3, 9, false], [201, 10, false], [3, 10001, false], [3, 10, true], [200, 10000, true], [200, 10000, true, 'a'], [201, 10, false, 'a'], [3, 10001, false, 'a']])('checks name %i and task %i bounds', async (nameSize, taskSize, accepted, character = '😀') => {
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ models: [{ id: 'fixture/model', name: 'Fixture' }] }) }).mockResolvedValueOnce({ ok: true, json: async () => ({ id: 'created' }) });
  vi.stubGlobal('fetch', fetcher); render(<Page />); await screen.findByText('Selected model: Fixture');
  fireEvent.change(screen.getByLabelText('Agent Name'), { target: { value: character.repeat(nameSize) } });
  fireEvent.change(screen.getByLabelText('What should your agent do?', { exact: false }), { target: { value: character.repeat(taskSize) } });
  fireEvent.submit(screen.getByLabelText('Agent Name').closest('form')!); await act(async () => {});
  expect(fetcher.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(accepted);
});
it('ignores an old response after a new catalog request succeeds', async () => {
  let resolveOld!: (value: unknown) => void;
  const old = new Promise(value => { resolveOld = value; });
  const fetcher = vi.fn().mockReturnValueOnce(old).mockResolvedValueOnce({ ok: true, json: async () => ({ models: [{ id: 'new/model', name: 'New' }] }) });
  vi.stubGlobal('fetch', fetcher);
  const hook = renderHook(({ enabled }) => useModelCatalog(enabled), { initialProps: { enabled: true } });
  hook.rerender({ enabled: false }); hook.rerender({ enabled: true });
  await waitFor(() => expect(hook.result.current.model).toBe('new/model'));
  await act(async () => { resolveOld({ ok: true, json: async () => ({ models: [{ id: 'old/model', name: 'Old' }] }) }); });
  expect(hook.result.current.model).toBe('new/model');
  expect(hook.result.current.state.models[0].id).toBe('new/model');
});
