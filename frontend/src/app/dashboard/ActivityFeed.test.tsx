// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ActivityFeed } from './ActivityFeed';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import type { ActivityEvent } from '@/lib/api';

vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn() }), usePathname: () => '/dashboard' }));
vi.mock('next/link', () => ({ default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));

afterEach(cleanup);

const event = (description: string): ActivityEvent => ({
  id: 'e1', agent_id: null, agent_name: null, action_type: 'heartbeat', description, ts: new Date().toISOString(), metadata: {},
} as ActivityEvent);

it('translates the heartbeat state in Russian', () => {
  const { container } = render(<LocaleProvider initialLocale="ru"><ActivityFeed activities={[event('Heartbeat: idle, 0 tasks, 0 notifications, 0 DMs')]} /></LocaleProvider>);
  const text = container.querySelector('p.break-words')?.textContent ?? '';
  expect(text).toContain('ожидает');
  expect(text).not.toMatch(/idle/);
});

it('keeps an unknown heartbeat state as written', () => {
  const { container } = render(<LocaleProvider initialLocale="ru"><ActivityFeed activities={[event('Heartbeat: working, 1 tasks, 0 notifications, 0 DMs')]} /></LocaleProvider>);
  expect(container.querySelector('p.break-words')?.textContent).toContain('working');
});
