// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { BlogMarkdown } from './BlogMarkdown';
import { BlogByline } from './BlogByline';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
afterEach(cleanup);
it('renders intact code and an accessible scrolling table, removes only a duplicated title', () => {
  const { container } = render(<BlogMarkdown title="Connect an agent" content={'# Connect an agent\n\n## Prepare\n\nRead the [guide](https://agentspore.com/docs/connect).\n\n```text\na  b  c\n```\n\n| Step | Result |\n| --- | --- |\n| Connect | Ready |'} />);
  expect(screen.queryByRole('heading', { name: 'Connect an agent' })).toBeNull();
  expect(screen.getByRole('heading', { name: 'Prepare' })).toBeTruthy();
  expect(screen.getByRole('region', { name: 'Article table' }).getAttribute('tabindex')).toBe('0');
  expect(screen.getByRole('table')).toBeTruthy();
  expect(container.querySelector('pre')?.textContent).toBe('a  b  c\n');
  expect(screen.getByRole('link', { name: 'guide' }).getAttribute('href')).toBe('https://agentspore.com/docs/connect');
});
it('retains a distinct leading heading and never executes raw HTML or unsafe links', () => {
  const { container } = render(<BlogMarkdown title="Article" content={'# A distinct heading\n\n<script>alert(1)</script>\n\n[unsafe](javascript:alert(1))'} />);
  expect(screen.getByRole('heading', { name: 'A distinct heading', level: 2 })).toBeTruthy();
  expect(container.querySelector('script')).toBeNull();
  expect(container.querySelector('a')?.getAttribute('href')).not.toContain('javascript:');
});
it('preserves GFM column alignment for both headers and cells', () => {
  render(<BlogMarkdown title="Prices" content={'| Item | Price |\n| :--- | ---: |\n| Tea | 5 |'} />);
  expect(screen.getByRole('columnheader', { name: 'Price' }).style.textAlign).toBe('right');
  expect(screen.getByRole('cell', { name: '5' }).style.textAlign).toBe('right');
  expect(screen.getByRole('columnheader', { name: 'Item' }).style.textAlign).toBe('left');
});

it.each(['en', 'ru'] as const)('keeps invalid publication dates explicit in %s', locale => {
  const post = { id: 'fixture', agent_id: 'fixture-agent', agent_name: 'Fixture Author', agent_handle: 'fixture',
    title: 'Fixture article', content: 'Useful prose.', created_at: 'invalid-date', reactions: { like: 0, fire: 0, insightful: 0, funny: 0 } };
  render(<LocaleProvider initialLocale={locale}><BlogByline post={post} /></LocaleProvider>);
  expect(screen.getByText(locale === 'ru' ? 'Дата публикации неизвестна' : 'Publication date unavailable')).toBeTruthy();
  expect(screen.queryByRole('time')).toBeNull();
  expect(screen.getByRole('link', { name: /Fixture Author/ }).getAttribute('href')).toBe('/agents/fixture-agent');
});
