const STOPS = new Set(['.', '!', '?', '。', '！', '？']);
const CJK_STOPS = new Set(['。', '！', '？']);
const CLOSERS = new Set(['"', "'", '”', '’', '»', ')', ']']);
const ABBREVIATIONS = new Set([
  'e.g.', 'i.e.', 'etc.', 'vs.', 'dr.', 'mr.', 'mrs.', 'ms.',
  'т.е.', 'т.д.', 'т.п.', 'др.', 'пр.', 'см.', 'г.', 'гг.',
]);
const length = (text: string) => Array.from(text).length;
const isSpace = (char: string) => /\s/u.test(char);

function isAbbreviation(chars: string[], stop: number): boolean {
  let start = stop;
  while (start > 0 && !isSpace(chars[start - 1] ?? '')) start--;
  const token = chars.slice(start, stop + 1).join('').toLowerCase().replace(/^["'“‘«([]+/u, '');
  return ABBREVIATIONS.has(token);
}

/** Exclusive end index of every sentence: a stop mark, optional closers, then whitespace or the end. */
function sentenceEnds(chars: string[]): number[] {
  const ends: number[] = [];
  for (let i = 0; i < chars.length; i++) {
    const char = chars[i] ?? '';
    if (!STOPS.has(char)) continue;
    let end = i + 1;
    while (end < chars.length && CLOSERS.has(chars[end] ?? '')) end++;
    const next = chars[end];
    if (next !== undefined && !isSpace(next) && !CJK_STOPS.has(char)) continue;
    if (char === '.' && isAbbreviation(chars, i)) continue;
    ends.push(end);
    i = end - 1;
  }
  return ends;
}

function lastSentenceWithin(text: string, limit: number): string {
  const chars = Array.from(text);
  const end = sentenceEnds(chars).filter(candidate => candidate <= limit && candidate >= limit / 2).pop();
  return end === undefined ? '' : chars.slice(0, end).join('').trimEnd();
}

/**
 * Cut text to `limit` characters: at the last sentence end that lies at least half of the limit in,
 * otherwise at a word with an ellipsis. Sentence ends come from a deterministic rule (same result on
 * server and client): `.`, `!`, `?` or `。` followed by whitespace; decimals, versions, URLs and a
 * short list of abbreviations are skipped.
 */
export function cutExcerpt(text: string, limit: number): string {
  if (length(text) <= limit) return text;
  return lastSentenceWithin(text, limit) || Array.from(text).slice(0, limit).join('').replace(/\s+\S*$/u, '') + '…';
}
