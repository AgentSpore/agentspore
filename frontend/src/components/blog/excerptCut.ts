const SENTENCE_END = /^[.!?]$/u;

/** Cut text to `limit` characters: at the last sentence end that fits, else at a word with an ellipsis. */
export function cutExcerpt(text: string, limit: number): string {
  const characters = Array.from(text);
  if (characters.length <= limit) return text;
  const isBoundary = (index: number) => SENTENCE_END.test(characters[index] ?? '') && /^\s$/u.test(characters[index + 1] ?? ' ');
  for (let index = limit - 1; index > 0; index -= 1) {
    if (isBoundary(index)) return characters.slice(0, index + 1).join('');
  }
  return characters.slice(0, limit).join('').replace(/\s+\S*$/u, '') + '…';
}
