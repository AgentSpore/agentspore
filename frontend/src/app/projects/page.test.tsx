// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProjectsPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { displayPublicLabel, publicMessages } from '@/lib/i18n/public';
import { sharedMessages } from '@/lib/i18n/shared';
import { LanguageSelector } from '@/components/LanguageSelector';
import { API_URL, type Project } from '@/lib/api';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
const resource = vi.hoisted<{ projects: Project[]; nullData: boolean; error: Error | null; lastUpdated: number | null }>(
  () => ({ projects: [], nullData: false, error: null, lastUpdated: null }),
);
vi.mock('@/components/Header', () => ({ Header: () => <LanguageSelector /> }));
vi.mock('@/hooks/usePolledResource', () => ({ usePolledResource: () => ({ data: resource.nullData ? null : resource.projects, error: resource.error, loading: false, lastUpdated: resource.lastUpdated, refetch: vi.fn() }) }));
beforeEach(() => { resource.projects = [PROJECT]; resource.nullData = false; resource.error = null; resource.lastUpdated = null; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(['en', 'ru'] as const)('names project search in %s', locale => {
  render(<LocaleProvider initialLocale={locale}><ProjectsPage /></LocaleProvider>);
  const search = screen.getByRole('textbox', { name: publicMessages[locale].search });
  fireEvent.change(search, { target: { value: 'No such project' } });
  expect(search).toHaveProperty('value', 'No such project');
  expect(screen.getByText(publicMessages[locale].noProjectsFound)).toBeTruthy();
});


const PROJECT: Project = {
  id: 'synthetic-project', title: 'Synthetic project', description: 'Synthetic description',
  category: 'productivity', status: 'deployed', votes_up: 2, votes_down: 1, score: 1,
  repo_url: 'https://example.invalid/repository', deploy_url: 'https://example.invalid/demo',
  tech_stack: ['TypeScript'], hackathon_id: null, creator_agent_id: 'synthetic-agent',
  agent_name: 'Synthetic agent', agent_handle: 'synthetic-agent', github_stars: 0,
  created_at: '2026-10-15T00:00:00Z',
};
const ACTIONS = {
  en: { up: 'Upvote Synthetic project', down: 'Downvote Synthetic project', repo: 'Open repository for Synthetic project', demo: 'Open demo for Synthetic project' },
  ru: { up: 'Поддержать проект Synthetic project', down: 'Проголосовать против проекта Synthetic project', repo: 'Открыть репозиторий проекта Synthetic project', demo: 'Открыть демо проекта Synthetic project' },
};

it.each(['en', 'ru'] as const)('exposes each project filter selection independently in %s', locale => {
  render(<LocaleProvider initialLocale={locale}><ProjectsPage /></LocaleProvider>);
  const cases = [
    { option: 'deployed', initial: 'all' }, { option: '24h', initial: 'all' },
    { option: 'stars', initial: 'new' }, { option: 'productivity', initial: 'all' },
  ];
  const selected: HTMLElement[] = [];
  for (const { option, initial } of cases) {
    const next = screen.getByRole('button', { name: displayPublicLabel(locale, option) });
    const group = next.parentElement;
    if (!group) throw new Error('Missing project filter container');
    const previous = within(group).getByRole('button', { name: displayPublicLabel(locale, initial) });
    expect(previous.getAttribute('aria-pressed')).toBe('true');
    for (const button of within(group).getAllByRole('button')) {
      expect(button.getAttribute('aria-pressed')).toBe(button === previous ? 'true' : 'false');
    }
    fireEvent.click(next);
    expect(previous.getAttribute('aria-pressed')).toBe('false');
    expect(next.getAttribute('aria-pressed')).toBe('true');
    selected.push(next);
    for (const button of selected) expect(button.getAttribute('aria-pressed')).toBe('true');
  }
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Synthetic draft' } });
  fireEvent.click(screen.getByRole('button', { name: locale === 'en' ? 'Русский' : 'English' }));
  for (const button of selected) expect(button.getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByRole('textbox')).toHaveProperty('value', 'Synthetic draft');
});

it.each(['en', 'ru'] as const)('renders independent named project actions in %s', locale => {
  resource.projects = [PROJECT];
  const { container } = render(<LocaleProvider initialLocale={locale}><ProjectsPage /></LocaleProvider>);
  const title = screen.getByRole('link', { name: PROJECT.title });
  expect(title.getAttribute('href')).toBe(`/projects/${PROJECT.id}`);
  for (const name of [ACTIONS[locale].up, ACTIONS[locale].down]) {
    expect(screen.getByRole('button', { name }).closest('a')).toBeNull();
  }
  for (const [name, url] of [[ACTIONS[locale].repo, PROJECT.repo_url], [ACTIONS[locale].demo, PROJECT.deploy_url]]) {
    const link = screen.getByRole('link', { name: name ?? '' });
    expect(link.parentElement?.closest('a')).toBeNull();
    expect(link.getAttribute('href')).toBe(url);
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  }
  expect(container.querySelector('a a, a button')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: locale === 'en' ? 'Русский' : 'English' }));
  const switched = locale === 'en' ? 'ru' : 'en';
  expect(screen.getByRole('button', { name: ACTIONS[switched].up })).toBeTruthy();
  expect(screen.getByRole('link', { name: ACTIONS[switched].repo }).getAttribute('href')).toBe(PROJECT.repo_url);
});

it.each([1, -1] as const)('preserves vote payload, pending lockout and updated counters for %s', async vote => {
  resource.projects = [PROJECT];
  let finish: (response: Response) => void = () => {};
  const fetchMock = vi.fn(() => new Promise<Response>(resolve => { finish = resolve; }));
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale="en"><ProjectsPage /></LocaleProvider>);
  const up = screen.getByRole('button', { name: ACTIONS.en.up });
  const down = screen.getByRole('button', { name: ACTIONS.en.down });
  const target = vote === 1 ? up : down;
  fireEvent.click(target);
  expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/api/v1/projects/${PROJECT.id}/vote`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ vote }),
  });
  expect(up).toHaveProperty('disabled', true);
  expect(down).toHaveProperty('disabled', true);
  fireEvent.click(target);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => { finish(new Response(JSON.stringify({ votes_up: 9, votes_down: 4 }))); });
  expect(up.textContent).toBe('↑9');
  expect(down.textContent).toBe('↓4');
  expect(up).toHaveProperty('disabled', false);
  expect(down).toHaveProperty('disabled', false);
});

it.each(['http', 'network'] as const)('preserves vote counters and releases lockout after %s failure', async failure => {
  resource.projects = [PROJECT];
  const fetchMock = vi.fn();
  if (failure === 'http') fetchMock.mockResolvedValue(new Response('', { status: 429 }));
  else fetchMock.mockRejectedValue(new TypeError('Synthetic network failure'));
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale="en"><ProjectsPage /></LocaleProvider>);
  const up = screen.getByRole('button', { name: ACTIONS.en.up });
  const down = screen.getByRole('button', { name: ACTIONS.en.down });
  await act(async () => { fireEvent.click(up); });
  expect(up.textContent).toBe('↑2');
  expect(down.textContent).toBe('↓1');
  expect(up).toHaveProperty('disabled', false);
  expect(down).toHaveProperty('disabled', false);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it.each(['en', 'ru'] as const)('preserves archived demo and absent URL guards in %s', locale => {
  resource.projects = [{ ...PROJECT, status: 'archived' }];
  const { unmount } = render(<LocaleProvider initialLocale={locale}><ProjectsPage /></LocaleProvider>);
  expect(screen.getByRole('link', { name: ACTIONS[locale].repo }).getAttribute('href')).toBe(PROJECT.repo_url);
  expect(screen.queryByRole('link', { name: ACTIONS[locale].demo })).toBeNull();
  unmount();
  resource.projects = [{ ...PROJECT, repo_url: null, deploy_url: null }];
  render(<LocaleProvider initialLocale={locale}><ProjectsPage /></LocaleProvider>);
  expect(screen.queryByRole('link', { name: ACTIONS[locale].repo })).toBeNull();
  expect(screen.queryByRole('link', { name: ACTIONS[locale].demo })).toBeNull();
});

it.each([true, false])('shows exactly one retry on a failed first load (null data: %s)', nullData => {
  resource.projects = []; resource.nullData = nullData; resource.error = new Error('HTTP 500');
  render(<LocaleProvider initialLocale="en"><ProjectsPage /></LocaleProvider>);
  expect(screen.getAllByRole('button', { name: sharedMessages.en.retry })).toHaveLength(1);
  expect(screen.getByRole('alert')).toBeTruthy();
});

it('keeps the freshness badge when only the search filter empties a loaded list', () => {
  resource.projects = [PROJECT]; resource.lastUpdated = Date.now();
  render(<LocaleProvider initialLocale="en"><ProjectsPage /></LocaleProvider>);
  fireEvent.change(screen.getByRole('textbox', { name: publicMessages.en.search }), { target: { value: 'no-such-project' } });
  expect(screen.getByText(publicMessages.en.noProjectsFound)).toBeTruthy();
  expect(screen.getByText(new RegExp(sharedMessages.en.updated))).toBeTruthy();
});

 it.each(['en', 'ru'] as const)('searches recovered projects without a known author in %s', locale => {
  resource.projects = [{ ...PROJECT, creator_agent_id: null, agent_name: null, recovery_provenance: { source: 'github', source_url: 'https://github.com/Example/history' } }];
  render(<LocaleProvider initialLocale={locale}><ProjectsPage /></LocaleProvider>);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'missing author' } });
  expect(screen.getByText(publicMessages[locale].noProjectsFound)).toBeTruthy();
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '' } });
  expect(screen.getByText(locale === 'en' ? 'Unknown author' : 'Автор неизвестен')).toBeTruthy();
  expect(screen.getByText(locale === 'en' ? 'Imported history' : 'Восстановленная история')).toBeTruthy();
});
