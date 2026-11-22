import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

// WP5/WP8: section headings are sans and sentence case, mono is for code and IDs.
it('agent page section h2s carry neither uppercase nor font-mono', () => {
  const source = readFileSync(new URL('./page.tsx', import.meta.url), 'utf8');
  const classes = [...source.matchAll(/<h2 className="([^"]*)"/g)].map(m => m[1] ?? '');
  expect(classes.length).toBeGreaterThanOrEqual(5);
  for (const cls of classes) {
    expect(cls).not.toMatch(/\buppercase\b/);
    expect(cls).not.toMatch(/\bfont-mono\b/);
    expect(cls).not.toMatch(/tracking-\[/);
  }
});
