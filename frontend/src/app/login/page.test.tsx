// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import LoginPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages, publicLabels, displayPublicLabel } from '@/lib/i18n/public';
import { accountMessages, displayAccountLabel } from '@/lib/i18n/account';
import { WORKSPACE_MESSAGES } from '@/lib/i18n/agentWorkspace';

const navigation = vi.hoisted(() => ({ query: '' }));
vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams(navigation.query), useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Toast', () => ({ useToast: () => ({ error: vi.fn(), info: vi.fn() }) }));


afterEach(() => { cleanup(); navigation.query = ''; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it.each([
  ['/workspace', '/workspace'],
  ['/workspace?view=tasks', '/workspace?view=tasks'],
  ['/projects', '/projects'],
  ['https://external.example.invalid/workspace', '/profile'],
  ['//external.example.invalid/workspace', '/profile'],
  ['/workspace-untrusted', '/profile'],
])('redirects a successful sign-in safely: %s', async (next, expected) => {
  navigation.query = new URLSearchParams({ next }).toString();
  const destination = { href: '' };
  vi.stubGlobal('window', new Proxy(window, {
    get: (target, property) => property === 'location' ? destination : Reflect.get(target, property),
  }));
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true, status: 200, json: async () => ({ access_token: 'synthetic-test-session' }),
  }));
  vi.stubGlobal('localStorage', { setItem: vi.fn() });
  render(<LocaleProvider initialLocale="en"><LoginPage /></LocaleProvider>);
  fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'synthetic@example.invalid' } });
  fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'synthetic-password' } });
  fireEvent.submit(screen.getByRole('button', { name: 'Sign In' }).closest('form')!);
  await waitFor(() => expect(destination.href).toBe(expected));
});

it.each(['en', 'ru'] as const)('associates login and registration fields with their visible labels in %s', locale => {
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  expect(screen.getByLabelText(accountMessages[locale].email)).toHaveProperty('type', 'email');
  expect(screen.getByLabelText(accountMessages[locale].password)).toHaveProperty('type', 'password');
  fireEvent.click(screen.getByRole('button', { name: accountMessages[locale].register }));
  expect(screen.getByLabelText(accountMessages[locale].name)).toHaveProperty('required', true);
  expect(screen.getByLabelText(accountMessages[locale].email)).toHaveProperty('type', 'email');
  expect(screen.getByLabelText(accountMessages[locale].password)).toHaveProperty('type', 'password');
});

it.each(['en', 'ru'] as const)('describes the external agent workspace inside the main landmark in %s', locale => {
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  expect(screen.getByRole('main').contains(screen.getByText(WORKSPACE_MESSAGES[locale].homeIntro))).toBe(true);
  expect(screen.queryByText(accountMessages[locale].aIAgentsBuildStartupsYouOwnAShare)).toBeNull();
});

  it('changes login labels while preserving entered values and does not submit', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<LocaleProvider initialLocale="en"><LoginPage /></LocaleProvider>);
    const email = screen.getByPlaceholderText('you@example.com');
    const password = screen.getByPlaceholderText('••••••••');
    fireEvent.change(email, { target: { value: 'synthetic@example.invalid' } });
    fireEvent.change(password, { target: { value: 'synthetic-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
    expect(screen.getByText('Продолжить с GitHub')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Войти' })).toBeTruthy();
    expect((email as HTMLInputElement).value).toBe('synthetic@example.invalid');
    expect((password as HTMLInputElement).value).toBe('synthetic-password');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('translates a local failure after switching without repeating authentication', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'log').mockImplementation(() => {});
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Synthetic network failure'));
    vi.stubGlobal('fetch', fetchMock);
    render(<LocaleProvider initialLocale="en"><LoginPage /></LocaleProvider>);
    fireEvent.submit(screen.getByRole('button', { name: 'Sign In' }).closest('form')!);
    await waitFor(() => expect(screen.getByText(accountMessages.en.networkErrorCannotReachServerCheckCORSAd)).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
    expect(screen.getByText(accountMessages.ru.networkErrorCannotReachServerCheckCORSAd)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('translates the HTTP fallback without resubmitting authentication', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'log').mockImplementation(() => {});
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchMock);
    render(<LocaleProvider initialLocale="en"><LoginPage /></LocaleProvider>);
    fireEvent.submit(screen.getByRole('button', { name: 'Sign In' }).closest('form')!);
    await waitFor(() => expect(screen.getByText('Authentication failed (HTTP 503)')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
    expect(screen.getByText('Не удалось войти (HTTP 503)')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('has matching domain keys and preserves unknown machine values', () => {
    for (const messages of [publicMessages, accountMessages, publicLabels]) {
      expect(Object.keys(messages.en).sort()).toEqual(Object.keys(messages.ru).sort());
      expect(Object.values(messages.en).every(value => typeof value === 'string' && value.length > 0)).toBe(true);
      expect(Object.values(messages.ru).every(value => typeof value === 'string' && value.length > 0)).toBe(true);
    }
    expect(displayPublicLabel('ru', 'archived')).toBe('В архиве');
    expect(displayPublicLabel('ru', 'unknown_status_42')).toBe('unknown_status_42');
    expect(displayAccountLabel('ru', 'Unrecognized API detail')).toBe('Unrecognized API detail');
    expect(displayAccountLabel('en', accountMessages.ru.networkError)).toBe(accountMessages.en.networkError);
  });

  it('preserves prototype-shaped API strings as strings in both domains', () => {
    for (const locale of ['en', 'ru'] as const) for (const value of ['toString', 'constructor', '__proto__', 'hasOwnProperty']) {
      expect(displayPublicLabel(locale, value)).toBe(value);
      expect(displayAccountLabel(locale, value)).toBe(value);
    }
  });

  it('renders decoded visible symbols rather than HTML entity syntax', () => {
    expect(publicMessages.en.x2B21GetSkillMd).toContain('⬡');
    expect(publicMessages.en.agentSporeXB7AutonomousStartupForgeXB7).toContain('·');
    expect(Object.values(publicMessages.en).every(value => !/&#(?:x[0-9a-f]+|[0-9]+);/i.test(value))).toBe(true);
  });


it.each(['en', 'ru'] as const)('exposes authentication mode without submitting or losing drafts in %s', locale => {
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  const signIn = screen.getByRole('button', { name: accountMessages[locale].signIn2 });
  const register = screen.getByRole('button', { name: accountMessages[locale].register });
  const email = screen.getByLabelText(accountMessages[locale].email);
  const password = screen.getByLabelText(accountMessages[locale].password);
  fireEvent.change(email, { target: { value: 'synthetic@example.invalid' } });
  fireEvent.change(password, { target: { value: 'synthetic-password' } });
  expect(signIn.getAttribute('aria-pressed')).toBe('true');
  expect(register.getAttribute('aria-pressed')).toBe('false');
  fireEvent.click(register);
  expect(register.getAttribute('aria-pressed')).toBe('true');
  expect(signIn.getAttribute('aria-pressed')).toBe('false');
  expect(screen.getByLabelText(accountMessages[locale].name)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: locale === 'en' ? 'Русский' : 'English' }));
  const switched = locale === 'en' ? 'ru' : 'en';
  expect(screen.getByRole('button', { name: accountMessages[switched].register }).getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: accountMessages[switched].signIn2 }));
  expect(screen.getByRole('button', { name: accountMessages[switched].signIn2 }).getAttribute('aria-pressed')).toBe('true');
  expect(screen.queryByLabelText(accountMessages[switched].name)).toBeNull();
  expect(email).toHaveProperty('value', 'synthetic@example.invalid');
  expect(password).toHaveProperty('value', 'synthetic-password');
  expect(fetchMock).not.toHaveBeenCalled();
});
