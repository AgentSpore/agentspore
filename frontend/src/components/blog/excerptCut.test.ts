import { expect, it } from 'vitest';
import { cutExcerpt } from './excerptCut';

it.each([
  ['en', 'First sentence is complete. Second one runs on and on without any stop until the limit is crossed here', 'First sentence is complete.'],
  ['en', 'Is it done? Yes it is! And then more words continue beyond the small limit without ending', 'Is it done? Yes it is!'],
  ['ru', 'Первое предложение закончено. Второе тянется дальше и дальше без остановки до самого предела', 'Первое предложение закончено.'],
])('cuts at the last sentence end within the limit (%s)', (_l, text, expected) => {
  expect(cutExcerpt(text, 60)).toBe(expected);
});
it('cuts at a word with an ellipsis when no sentence end fits', () => {
  expect(cutExcerpt('alpha beta gamma delta epsilon zeta eta theta', 20)).toBe('alpha beta gamma…');
  expect(cutExcerpt('альфа бета гамма дельта эпсилон дзета эта тета', 20)).toBe('альфа бета гамма…');
});
it('does not treat a decimal point as a sentence end', () => {
  expect(cutExcerpt('Version 3.5 shipped with many changes for everybody here', 20)).toBe('Version 3.5 shipped…');
});
it('leaves short text untouched', () => {
  expect(cutExcerpt('Short text', 280)).toBe('Short text');
});
