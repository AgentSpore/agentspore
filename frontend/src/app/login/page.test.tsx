// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import LoginPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages, publicLabels, displayPublicLabel } from '@/lib/i18n/public';
import { accountMessages, displayAccountLabel } from '@/lib/i18n/account';
import { WORKSPACE_MESSAGES } from '@/lib/i18n/agentWorkspace';

const navigation = vi.hoisted(() => ({ query: '' }));
vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams(navigation.query), useRouter: () => ({ refresh: vi.fn() }), usePathname: () => '/login' }));
vi.mock('@/components/Toast', () => ({ useToast: () => ({ error: vi.fn(), info: vi.fn() }) }));


/** Calls other than the public OAuth-provider lookup, i.e. real authentication requests. */
const authCalls = (mock: { mock: { calls: unknown[][] } }) =>
  mock.mock.calls.filter(([url]) => !String(url).endsWith('/oauth/providers'));

const providersFetch = (flags: { github: boolean; google: boolean }) =>
  vi.fn<typeof fetch>(async () => ({ ok: true, status: 200, json: async () => flags }) as Response);

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
  });
});

afterEach(() => { cleanup(); navigation.query = ''; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it.each(['en', 'ru'] as const)('opens registration from its incoming link in %s', locale => {
  navigation.query = 'tab=register&next=%2Fworkspace';
  vi.stubGlobal('fetch', providersFetch({ github: false, google: false }));
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(accountMessages[locale].createAccount);
  expect(screen.getByLabelText(accountMessages[locale].name)).toHaveProperty('required', true);
  expect(screen.getByLabelText(accountMessages[locale].password).getAttribute('autocomplete')).toBe('new-password');
});

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
  vi.stubGlobal('localStorage', { setItem: vi.fn(), getItem: vi.fn(() => null) });
  render(<LocaleProvider initialLocale="en"><LoginPage /></LocaleProvider>);
  fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'synthetic@example.invalid' } });
  fireEvent.change(screen.getByPlaceholderText('Enter your password'), { target: { value: 'synthetic-password' } });
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

  it('changes login labels while preserving entered values and does not submit', async () => {
    const fetchMock = providersFetch({ github: true, google: false });
    vi.stubGlobal('fetch', fetchMock);
    render(<LocaleProvider initialLocale="en"><LoginPage /></LocaleProvider>);
    const email = screen.getByPlaceholderText('you@example.com');
    const password = screen.getByPlaceholderText('Enter your password');
    fireEvent.change(email, { target: { value: 'synthetic@example.invalid' } });
    fireEvent.change(password, { target: { value: 'synthetic-password' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Русский' })[0]);
    expect(await screen.findByText('Продолжить с GitHub')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Войти' })).toBeTruthy();
    expect((email as HTMLInputElement).value).toBe('synthetic@example.invalid');
    expect((password as HTMLInputElement).value).toBe('synthetic-password');
    expect(authCalls(fetchMock)).toHaveLength(0);
  });

  it('translates a local failure after switching without repeating authentication', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'log').mockImplementation(() => {});
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Synthetic network failure'));
    vi.stubGlobal('fetch', fetchMock);
    render(<LocaleProvider initialLocale="en"><LoginPage /></LocaleProvider>);
    fireEvent.submit(screen.getByRole('button', { name: 'Sign In' }).closest('form')!);
    await waitFor(() => expect(screen.getByText(accountMessages.en.networkErrorCannotReachServerCheckCORSAd)).toBeTruthy());
    fireEvent.click(screen.getAllByRole('button', { name: 'Русский' })[0]);
    expect(screen.getByText(accountMessages.ru.networkErrorCannotReachServerCheckCORSAd)).toBeTruthy();
    expect(authCalls(fetchMock)).toHaveLength(1);
  });

  it('translates the HTTP fallback without resubmitting authentication', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'log').mockImplementation(() => {});
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchMock);
    render(<LocaleProvider initialLocale="en"><LoginPage /></LocaleProvider>);
    fireEvent.submit(screen.getByRole('button', { name: 'Sign In' }).closest('form')!);
    await waitFor(() => expect(screen.getByText('Authentication failed (HTTP 503)')).toBeTruthy());
    fireEvent.click(screen.getAllByRole('button', { name: 'Русский' })[0]);
    expect(screen.getByText('Не удалось войти (HTTP 503)')).toBeTruthy();
    expect(authCalls(fetchMock)).toHaveLength(1);
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
  fireEvent.click(screen.getAllByRole('button', { name: locale === 'en' ? 'Русский' : 'English' })[0]);
  const switched = locale === 'en' ? 'ru' : 'en';
  expect(screen.getByRole('button', { name: accountMessages[switched].register }).getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: accountMessages[switched].signIn2 }));
  expect(screen.getByRole('button', { name: accountMessages[switched].signIn2 }).getAttribute('aria-pressed')).toBe('true');
  expect(screen.queryByLabelText(accountMessages[switched].name)).toBeNull();
  expect(email).toHaveProperty('value', 'synthetic@example.invalid');
  expect(password).toHaveProperty('value', 'synthetic-password');
  expect(authCalls(fetchMock)).toHaveLength(0);
});

it.each(['en', 'ru'] as const)('offers only the configured OAuth providers in %s', async locale => {
  vi.stubGlobal('fetch', providersFetch({ github: false, google: true }));
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  expect(await screen.findByText(accountMessages[locale].continueWithGoogle)).toBeTruthy();
  expect(screen.queryByText(accountMessages[locale].continueWithGitHub)).toBeNull();
  expect(screen.getByText(accountMessages[locale].orSignInWith)).toBeTruthy();
});

it.each(['en', 'ru'] as const)('hides the OAuth block and the divider when no provider is configured in %s', async locale => {
  const fetchMock = providersFetch({ github: false, google: false });
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(document.querySelector('a[href$="/oauth/github"], a[href$="/oauth/google"]')).toBeNull();
  expect(screen.queryByText(accountMessages[locale].orSignInWith)).toBeNull();
  expect(screen.getByLabelText(accountMessages[locale].email)).toBeTruthy();
});


it.each(['en', 'ru'] as const)('explains account access with shared navigation and a mode heading in %s', locale => {
  vi.stubGlobal('fetch', providersFetch({ github: false, google: false }));
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  const main = screen.getByRole('main');
  const header = screen.getByRole('banner');
  expect(main.contains(header)).toBe(false);
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(accountMessages[locale].signIn);
  expect(screen.getByText(locale === 'en'
    ? 'Sign in to see your connected agents, their tasks and results.'
    : 'Войдите, чтобы видеть подключённых агентов, их задачи и результаты.')).toBeTruthy();
  expect(screen.getByText(locale === 'en'
    ? 'You can use the services without an AgentSpore account.'
    : 'Сервисами можно пользоваться без аккаунта AgentSpore.')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: accountMessages[locale].register }));
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(accountMessages[locale].createAccount);
});

it.each(['en', 'ru'] as const)('supports password managers for each authentication mode in %s', locale => {
  vi.stubGlobal('fetch', providersFetch({ github: false, google: false }));
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  const email = screen.getByLabelText(accountMessages[locale].email);
  const password = screen.getByLabelText(accountMessages[locale].password);
  expect(email.getAttribute('autocomplete')).toBe('email');
  expect(password.getAttribute('autocomplete')).toBe('current-password');
  fireEvent.click(screen.getByRole('button', { name: accountMessages[locale].register }));
  expect(screen.getByLabelText(accountMessages[locale].name).getAttribute('autocomplete')).toBe('name');
  expect(password.getAttribute('autocomplete')).toBe('new-password');
});

it.each(['en', 'ru'] as const)('toggles password visibility without losing drafts, focus or submitting in %s', locale => {
  const fetchMock = providersFetch({ github: false, google: false });
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  const password = screen.getByLabelText(accountMessages[locale].password);
  fireEvent.change(password, { target: { value: 'synthetic-password' } });
  password.focus();
  const toggle = screen.getByRole('button', { name: locale === 'en' ? 'Show password' : 'Показать пароль' });
  expect(toggle.getAttribute('type')).toBe('button');
  expect(toggle.getAttribute('aria-pressed')).toBe('false');
  fireEvent.mouseDown(toggle);
  fireEvent.click(toggle);
  expect(password).toHaveProperty('type', 'text');
  expect(password).toHaveProperty('value', 'synthetic-password');
  expect(document.activeElement).toBe(password);
  expect(toggle.getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(screen.getByRole('button', { name: locale === 'en' ? 'Hide password' : 'Скрыть пароль' }));
  expect(password).toHaveProperty('type', 'password');
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole('button', { name: accountMessages[locale].register }));
  expect(password).toHaveProperty('type', 'password');
  expect(password).toHaveProperty('value', 'synthetic-password');
  expect(authCalls(fetchMock)).toHaveLength(0);
});


it.each(['en', 'ru'] as const)('keeps login usable without storage and does not fetch a private profile in %s', locale => {
  const fetchMock = providersFetch({ github: false, google: false });
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('localStorage', { getItem: () => { throw new DOMException('Synthetic storage denied', 'SecurityError'); } });
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(accountMessages[locale].signIn);
  expect(screen.getByLabelText(accountMessages[locale].email)).toBeTruthy();
  expect(screen.getByLabelText(accountMessages[locale].password)).toHaveProperty('type', 'password');
  expect(authCalls(fetchMock)).toHaveLength(0);
  expect(fetchMock.mock.calls.every(([, options]) => !new Headers(options?.headers).has('Authorization'))).toBe(true);
});

it('hides the next login password after registration requests email verification', async () => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  const fetchMock = vi.fn<typeof fetch>(async url => String(url).endsWith('/oauth/providers')
    ? new Response(JSON.stringify({ github: false, google: false }))
    : new Response(JSON.stringify({ requires_verification: true }), { status: 202 }));
  vi.stubGlobal('fetch', fetchMock);
  render(<LocaleProvider initialLocale="en"><LoginPage /></LocaleProvider>);
  fireEvent.click(screen.getByRole('button', { name: accountMessages.en.register }));
  fireEvent.change(screen.getByLabelText(accountMessages.en.name), { target: { value: 'Synthetic name' } });
  fireEvent.change(screen.getByLabelText(accountMessages.en.email), { target: { value: 'synthetic@example.invalid' } });
  const password = screen.getByLabelText(accountMessages.en.password);
  fireEvent.change(password, { target: { value: 'synthetic-password' } });
  fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
  expect(password).toHaveProperty('type', 'text');
  fireEvent.submit(screen.getByRole('button', { name: 'Create Account' }).closest('form')!);
  await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Sign In'));
  expect(password).toHaveProperty('type', 'password');
  expect(password).toHaveProperty('value', '');
  expect(screen.getByLabelText(accountMessages.en.email)).toHaveProperty('value', 'synthetic@example.invalid');
  fireEvent.change(password, { target: { value: 'synthetic-next-password' } });
  expect(password).toHaveProperty('type', 'password');
  expect(authCalls(fetchMock)).toHaveLength(1);
});

it('keeps a visible focus ring on the fields and a text-only password placeholder', () => {
  render(<LocaleProvider initialLocale="en"><LoginPage /></LocaleProvider>);
  for (const field of [screen.getByPlaceholderText('you@example.com'), screen.getByPlaceholderText('Enter your password')]) {
    expect(field.className).not.toContain('outline-none');
    expect(field.className).toContain('focus-visible:outline-2');
  }
});
