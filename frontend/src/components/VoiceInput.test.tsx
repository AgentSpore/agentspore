// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
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
