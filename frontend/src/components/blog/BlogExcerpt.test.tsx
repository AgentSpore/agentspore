// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { BlogExcerpt } from './BlogExcerpt';

afterEach(cleanup);
it('keeps excerpts plain, bounded and outside fenced code, tables and HTML', () => {
  const prose = 'Useful text with [a guide](https://agentspore.com/docs/connect), **clear steps**, and `code`. ';
  const { container } = render(<BlogExcerpt content={`# Heading\n\n<script>bad()</script>\n\n\`\`\`json\n{"raw": 1}\n\`\`\`\n\n| raw |\n| --- |\n| table |\n\n${prose.repeat(8)}`} />);
  const text = container.textContent ?? '';
  expect(text).toContain('Useful text with a guide, clear steps, and code.');
  expect(text.endsWith('…')).toBe(true);
  expect(Array.from(text).length).toBeLessThanOrEqual(281);
  expect(container.querySelector('a, code, pre, table, script, strong, h1')).toBeNull();
  expect(text).not.toContain('bad()');
});
it('does not split a Unicode character while bounding the excerpt', () => {
  const { container } = render(<BlogExcerpt content={'🪴'.repeat(300)} />);
  expect(container.textContent).toBe('🪴'.repeat(280) + '…');
});
it('does not promote a code-only post to prose', () => {
  const { container } = render(<BlogExcerpt content={'```\nOnly a code sample\n```'} />);
  expect(container.textContent).toBe('');
});
it('accepts a layout class for compact cards', () => {
  const { container } = render(<BlogExcerpt content="Short text" className="line-clamp-3" />);
  expect((container.firstChild as HTMLElement).className).toContain('line-clamp-3');
});
