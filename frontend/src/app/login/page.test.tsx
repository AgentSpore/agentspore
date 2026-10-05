// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import LoginPage from './page';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { publicMessages, publicLabels, displayPublicLabel } from '@/lib/i18n/public';
import { accountMessages, displayAccountLabel } from '@/lib/i18n/account';

vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams(), useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/components/Toast', () => ({ useToast: () => ({ error: vi.fn(), info: vi.fn() }) }));


afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

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
