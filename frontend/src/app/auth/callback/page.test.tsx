// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { rememberClaimReturn } from '@/lib/agent-claim';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import AuthCallback from './page';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); sessionStorage.clear(); });

function signInViaOAuth(remembered: string | null) {
  const destination = { href: '' };
  vi.stubGlobal('window', new Proxy(window, {
    get: (target, property) => property === 'location' ? destination : Reflect.get(target, property),
  }));
  vi.stubGlobal('requestAnimationFrame', (run: () => void) => { run(); return 1; });
  vi.stubGlobal('localStorage', { setItem: vi.fn(), getItem: vi.fn(() => null), removeItem: vi.fn() });
  Object.defineProperty(destination, 'search', { value: '?access_token=synthetic-a&refresh_token=synthetic-r' });
  Object.defineProperty(destination, 'pathname', { value: '/auth/callback' });
  Object.defineProperty(destination, 'origin', { value: 'https://app.example.invalid' });
  if (remembered) rememberClaimReturn(remembered);
  render(<LocaleProvider initialLocale="en"><AuthCallback /></LocaleProvider>);
  return destination;
}

it('returns a signed-out owner to the email-change confirmation after OAuth', () => {
  expect(signInViaOAuth('/confirm-email-change').href).toBe('https://app.example.invalid/confirm-email-change');
});

it('lands on the profile when nothing was remembered', () => {
  expect(signInViaOAuth(null).href).toBe('https://app.example.invalid/profile');
});
