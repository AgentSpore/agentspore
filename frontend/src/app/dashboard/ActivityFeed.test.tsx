// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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

afterEach(() => { vi.restoreAllMocks(); });

it('fades the clipped bottom row only while more rows are scrollable', () => {
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(100);
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(500);
  const { container } = render(<LocaleProvider initialLocale="en"><ActivityFeed activities={[event('Heartbeat: idle, 0 tasks, 0 notifications, 0 DMs')]} /></LocaleProvider>);
  const region = container.querySelector('[role="region"]') as HTMLElement;
  expect(region.dataset.moreBelow).toBe('true');
  region.scrollTop = 400;
  fireEvent.scroll(region);
  expect(region.dataset.moreBelow).toBe('false');
});

it('re-syncs the fade when the rows or the filter change without a scroll', () => {
  let scrollHeight = 500;
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(100);
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(() => scrollHeight);
  const ui = (rows: ActivityEvent[]) => <LocaleProvider initialLocale="en"><ActivityFeed activities={rows} /></LocaleProvider>;
  const first = [event('Heartbeat: idle, 0 tasks, 0 notifications, 0 DMs')];
  const { container, rerender } = render(ui(first));
  const region = () => container.querySelector('[role="region"]') as HTMLElement;
  expect(region().dataset.moreBelow).toBe('true');
  scrollHeight = 100;
  rerender(ui([...first, event('Heartbeat: active, 1 tasks, 0 notifications, 0 DMs')]));
  expect(region().dataset.moreBelow).toBe('false');
  scrollHeight = 500;
  fireEvent.click(screen.getByRole('button', { name: 'Actions' }));
  expect(region().dataset.moreBelow).toBe('true');
});
