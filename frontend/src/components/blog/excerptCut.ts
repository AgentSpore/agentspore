const TERMINAL = /[.!?。！？]["'”’»)\]]*\s*$/u;
const length = (text: string) => Array.from(text).length;

function lastSentenceWithin(text: string, limit: number): string {
  if (typeof Intl.Segmenter !== 'function') return '';
  let consumed = 0, best = '', prefix = '';
  for (const { segment } of new Intl.Segmenter('en', { granularity: 'sentence' }).segment(text)) {
    consumed += length(segment);
    if (consumed > limit) break;
    prefix += segment;
    const trimmed = prefix.trimEnd();
    if (TERMINAL.test(segment) && length(trimmed) >= limit / 2) best = trimmed;
  }
  return best;
}

/**
 * Cut text to `limit` characters: at the last sentence end (more than half of the limit in),
 * otherwise at a word with an ellipsis. Sentence ends come from `Intl.Segmenter`, so
 * abbreviations, decimals and URLs are not cut; without it the word cut is used.
 */
export function cutExcerpt(text: string, limit: number): string {
  if (length(text) <= limit) return text;
  return lastSentenceWithin(text, limit) || Array.from(text).slice(0, limit).join('').replace(/\s+\S*$/u, '') + '…';
}
