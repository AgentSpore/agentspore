// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { hydrateRoot, type Root } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
import { VoiceInput } from './VoiceInput';
import { LanguageSelector } from './LanguageSelector';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { sharedMessages } from '@/lib/i18n/shared';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('changes the next recognition language without recreating or aborting the instance', () => {
  const created: SyntheticRecognition[] = [];
  class SyntheticRecognition {
    lang = '';
    onresult: (event: { results: { transcript: string }[][] }) => void = () => {};
    start = vi.fn();
    stop = vi.fn();
    abort = vi.fn();
    constructor() { created.push(this); }
  }
  vi.stubGlobal('SpeechRecognition', SyntheticRecognition);
  const onTranscript = vi.fn();
  render(<LocaleProvider initialLocale="en"><LanguageSelector /><VoiceInput onTranscript={onTranscript} /></LocaleProvider>);
  expect(created[0].lang).toBe('en-US');
  fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
  expect(created).toHaveLength(1);
  expect(created[0].abort).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: sharedMessages.ru.voiceInput }));
  expect(created[0].lang).toBe('ru-RU');
  expect(created[0].start).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: sharedMessages.ru.stopRecording }));
  expect(created[0].stop).toHaveBeenCalledTimes(1);
  act(() => { created[0].onresult({ results: [[{ transcript: 'Overview' }]] }); });
  expect(onTranscript).toHaveBeenCalledWith('Overview');
});


const recognizers: BrowserRecognition[] = [];
class BrowserRecognition {
  continuous = false;
  interimResults = false;
  lang = '';
  maxAlternatives = 1;
  onresult: (event: { results: { transcript: string }[][] }) => void = () => {};
  onerror: () => void = () => {};
  onend: () => void = () => {};
  start = vi.fn();
  stop = vi.fn();
  abort = vi.fn();
  constructor() { recognizers.push(this); }
}

beforeEach(() => {
  recognizers.length = 0;
  vi.stubGlobal('SpeechRecognition', undefined);
  vi.stubGlobal('webkitSpeechRecognition', undefined);
});

it('hides the voice button when neither recognition constructor exists', () => {
  render(<LocaleProvider initialLocale="en"><VoiceInput onTranscript={() => {}} /></LocaleProvider>);
  expect(screen.queryByRole('button')).toBeNull();
  expect(recognizers).toHaveLength(0);
});

it.each(['SpeechRecognition', 'webkitSpeechRecognition'])(
  'starts and aborts the available %s constructor', constructor => {
    vi.stubGlobal(constructor, BrowserRecognition);
    const { unmount } = render(<LocaleProvider initialLocale="en"><VoiceInput onTranscript={() => {}} /></LocaleProvider>);
    expect(recognizers).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: sharedMessages.en.voiceInput }));
    expect(recognizers[0].start).toHaveBeenCalledTimes(1);
    unmount();
    expect(recognizers[0].abort).toHaveBeenCalledTimes(1);
  }
);

it('prefers the native constructor when the prefixed constructor also exists', () => {
  vi.stubGlobal('SpeechRecognition', BrowserRecognition);
  const prefixed = vi.fn(function () { return new BrowserRecognition(); });
  vi.stubGlobal('webkitSpeechRecognition', prefixed);
  render(<LocaleProvider initialLocale="en"><VoiceInput onTranscript={() => {}} /></LocaleProvider>);
  expect(recognizers).toHaveLength(1);
  expect(prefixed).not.toHaveBeenCalled();
});

it.each([
  { results: [[{ transcript: '  Overview  ' }]], expected: ['Overview'] },
  { results: [[{ transcript: '   ' }]], expected: [] },
  { results: [], expected: [] },
])('delivers only nonempty trimmed recognition results: $expected', ({ results, expected }) => {
  vi.stubGlobal('SpeechRecognition', BrowserRecognition);
  const onTranscript = vi.fn();
  render(<LocaleProvider initialLocale="en"><VoiceInput onTranscript={onTranscript} /></LocaleProvider>);
  fireEvent.click(screen.getByRole('button', { name: sharedMessages.en.voiceInput }));
  act(() => { recognizers[0].onresult({ results }); });
  expect(onTranscript.mock.calls).toEqual(expected.map(text => [text]));
  expect(screen.getByRole('button', { name: sharedMessages.en.voiceInput })).toBeTruthy();
});

it.each(['onerror', 'onend'] as const)('returns to idle on recognition %s', event => {
  vi.stubGlobal('SpeechRecognition', BrowserRecognition);
  render(<LocaleProvider initialLocale="en"><VoiceInput onTranscript={() => {}} /></LocaleProvider>);
  fireEvent.click(screen.getByRole('button', { name: sharedMessages.en.voiceInput }));
  expect(screen.getByRole('button', { name: sharedMessages.en.stopRecording })).toBeTruthy();
  act(() => { recognizers[0][event](); });
  expect(screen.getByRole('button', { name: sharedMessages.en.voiceInput })).toBeTruthy();
});

it('keeps a disabled voice button from starting recognition', () => {
  vi.stubGlobal('SpeechRecognition', BrowserRecognition);
  render(<LocaleProvider initialLocale="en"><VoiceInput disabled onTranscript={() => {}} /></LocaleProvider>);
  fireEvent.click(screen.getByRole('button', { name: sharedMessages.en.voiceInput }));
  expect(recognizers[0].start).not.toHaveBeenCalled();
});

it('aborts the old recognizer and delivers results to a replacement callback', () => {
  vi.stubGlobal('SpeechRecognition', BrowserRecognition);
  const original = vi.fn(), replacement = vi.fn();
  const { rerender } = render(<LocaleProvider initialLocale="en"><VoiceInput onTranscript={original} /></LocaleProvider>);
  rerender(<LocaleProvider initialLocale="en"><VoiceInput onTranscript={replacement} /></LocaleProvider>);
  expect(recognizers).toHaveLength(2);
  expect(recognizers[0].abort).toHaveBeenCalledTimes(1);
  act(() => { recognizers[1].onresult({ results: [[{ transcript: 'Latest' }]] }); });
  expect(replacement).toHaveBeenCalledWith('Latest');
  expect(original).not.toHaveBeenCalled();
});

it.each([true, false])('hydrates an empty server snapshot with browser support=%s', async supported => {
  vi.stubGlobal('SpeechRecognition', supported ? BrowserRecognition : undefined);
  const tree = <LocaleProvider initialLocale="en"><VoiceInput onTranscript={() => {}} /></LocaleProvider>;
  const markup = renderToString(tree);
  expect(markup).toBe('');
  const host = document.createElement('div');
  host.innerHTML = markup;
  document.body.append(host);
  const errors: unknown[] = [];
  let root: Root | undefined;
  try {
    await act(async () => {
      root = hydrateRoot(host, tree, { onRecoverableError: error => { errors.push(error); } });
    });
    expect(errors).toEqual([]);
    expect(Boolean(screen.queryByRole('button'))).toBe(supported);
  } finally {
    act(() => { root?.unmount(); });
    host.remove();
  }
});
