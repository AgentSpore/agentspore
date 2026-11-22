import { expect, it } from 'vitest';
import { cutExcerpt } from './excerptCut';

const LIMIT = 280;
const words = (count: number, prefix = 'word') => Array.from({ length: count }, (_, index) => `${prefix}${index}`).join(' ');
const tail = ` Then ${words(80)}`;
it.each([
  ['e.g.', 'e.g.'],
  ['i.e.', 'i.e.'],
  ['т.е.', 'т.е.'],
  ['т.д.', 'т.д.'],
  ['decimal', '3.5'],
  ['version', 'v1.2'],
  ['url', 'https://example.com/a.html'],
])('does not cut at an abbreviation or number (%s)', (_name, token) => {
  const result = cutExcerpt(`${words(22)} ${token} ${words(40)}`, LIMIT);
  expect(result.endsWith('…')).toBe(true);
  expect(result.endsWith(token)).toBe(false);
  expect(result).toContain(token);
});

it('ends on a closing quote or parenthesis after the period', () => {
  const quoted = `${words(22)} He said "it is done."`;
  expect(cutExcerpt(quoted + tail, LIMIT)).toBe(quoted);
  const paren = `${words(22)} (and that is all.)`;
  expect(cutExcerpt(paren + tail, LIMIT)).toBe(paren);
});

it('recognises the CJK full stop', () => {
  const first = '这是一个很长的句子'.repeat(20) + '。';
  expect(cutExcerpt(first + '另一个句子'.repeat(40), LIMIT)).toBe(first);
});

it('keeps both sentences when both fit, with ! and ?', () => {
  const text = `${words(12)} is it done? ${words(12)} yes it is!${tail}`;
  expect(cutExcerpt(text, LIMIT)).toBe(`${words(12)} is it done? ${words(12)} yes it is!`);
});

it('refuses a very short first sentence and cuts at a word instead', () => {
  const result = cutExcerpt(`Hi! ${words(80)}`, LIMIT);
  expect(result.endsWith('…')).toBe(true);
  expect(Array.from(result).length).toBeGreaterThan(LIMIT / 2);
  expect(Array.from(result).length).toBeLessThanOrEqual(LIMIT + 1);
});

it('cuts at a word with an ellipsis when no sentence end fits, in English and Russian', () => {
  expect(cutExcerpt('alpha beta gamma delta epsilon zeta eta theta', 20)).toBe('alpha beta gamma…');
  expect(cutExcerpt('альфа бета гамма дельта эпсилон дзета эта тета', 20)).toBe('альфа бета гамма…');
});

it('leaves short text untouched', () => {
  expect(cutExcerpt('Short text', LIMIT)).toBe('Short text');
});
