// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import LoginPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { accountMessages } from '@/lib/i18n/account';
import { AUTH_FLOW_MESSAGES } from '@/lib/i18n/authFlow';
import { tokenMessages } from '@/lib/i18n/tokenPage';

const navigation = vi.hoisted(() => ({ query: '' }));
vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams(navigation.query), useRouter: () => ({ refresh: vi.fn() }), usePathname: () => '/login' }));
vi.mock('@/components/Toast', () => ({ useToast: () => ({ error: vi.fn(), info: vi.fn() }) }));

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => ({ ok: true, status: 200, json: async () => ({ github: false, google: false }) }) as Response));
});
afterEach(() => { cleanup(); navigation.query = ''; vi.unstubAllGlobals(); });

const RULES = ['briefNeeds', 'briefOne', 'briefReview', 'briefPayout'] as const;
const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

it.each(['en', 'ru'] as const)('orders title, one intro, reward banner, tabs, fields, submit in %s', locale => {
  navigation.query = 'tab=register';
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  const main = screen.getByRole('main');
  const title = screen.getByRole('heading', { level: 1 });
  const intro = screen.getByText(AUTH_FLOW_MESSAGES[locale].registerSubtitle);
  const banner = screen.getByRole('heading', { name: tokenMessages[locale].briefTitle });
  const tab = screen.getByRole('button', { name: AUTH_FLOW_MESSAGES[locale].tabRegister });
  const email = screen.getByLabelText(accountMessages[locale].email);
  const submit = screen.getAllByRole('button', { name: accountMessages[locale].createAccount }).find(b => b.getAttribute('type') === 'submit')!;
  [title, intro, banner, tab, email, submit].reduce((prev, next) => { expect(follows(prev, next)).toBe(true); return next; });
  expect(main.querySelectorAll('h1 + p').length).toBe(1);
  expect(main.querySelectorAll('h1 ~ p').length).toBe(1);
  expect(screen.queryByText(accountMessages[locale].servicesWithoutAccount)).toBeNull();
});

it.each(['en', 'ru'] as const)('keeps the reward rules and token link inside a disclosure in %s', locale => {
  navigation.query = 'tab=register';
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  const details = screen.getByRole('main').querySelector('details')!;
  expect(details).toBeTruthy();
  expect(details.open).toBe(false);
  expect(screen.getByText(tokenMessages[locale].briefCompact)).toBeTruthy();
  expect(screen.queryByText(tokenMessages[locale].briefIntro)).toBeNull();
  expect(details.querySelector('summary')?.textContent).toBe(tokenMessages[locale].briefDetails);
  for (const key of RULES) expect(details.textContent).toContain(tokenMessages[locale][key]);
  expect(details.querySelector('a')?.getAttribute('href')).toBe('/token');
  expect(details.querySelector('a')?.textContent).toBe(tokenMessages[locale].briefLink);
});

it.each(['en', 'ru'] as const)('title and submit follow the active tab in %s', locale => {
  render(<LocaleProvider initialLocale={locale}><LoginPage /></LocaleProvider>);
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(accountMessages[locale].signIn);
  expect(screen.getByRole('main').querySelector('details')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: AUTH_FLOW_MESSAGES[locale].tabRegister }));
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(accountMessages[locale].createAccount);
  expect(screen.getByRole('button', { name: accountMessages[locale].createAccount }).getAttribute('type')).toBe('submit');
});

it('uses sentence case for the English register label', () => {
  expect(accountMessages.en.createAccount).toBe('Create account');
});
