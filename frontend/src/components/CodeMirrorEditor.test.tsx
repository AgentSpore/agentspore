// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { EditorView } from '@codemirror/view';
import CodeMirrorEditor from './CodeMirrorEditor';
import { LanguageSelector } from './LanguageSelector';
import { LocaleProvider } from '@/lib/i18n/LocaleProvider';
import { editorPhrases, sharedMessages, displaySharedText } from '@/lib/i18n/shared';
import { hostedAgentMessages, displayHostedAgentText } from '@/lib/i18n/hostedAgent';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('reconfigures the real editor without losing code, selection or calling onChange', () => {
  const onChange = vi.fn();
  const code = 'const title = "Overview";';
  render(<LocaleProvider initialLocale="en"><LanguageSelector /><CodeMirrorEditor value={code} filePath="synthetic.ts" onChange={onChange} /></LocaleProvider>);
  const content = screen.getByRole('textbox', { name: 'Code editor' });
  const editor = EditorView.findFromDOM(content)!;
  act(() => { editor.dispatch({ selection: { anchor: 7 } }); });
  fireEvent.click(screen.getByRole('button', { name: 'Русский' }));
  expect(EditorView.findFromDOM(content)).toBe(editor);
  expect(editor.state.doc.toString()).toBe(code);
  expect(editor.state.selection.main.anchor).toBe(7);
  expect(editor.state.phrase('Fold line')).toBe('Свернуть строку');
  expect(screen.getByRole('textbox', { name: sharedMessages.ru.codeEditor })).toBe(content);
  expect(onChange).not.toHaveBeenCalled();
});

it('keeps both locale dictionaries complete and unknown data unchanged', () => {
  for (const dictionary of [sharedMessages, hostedAgentMessages, editorPhrases]) {
    expect(Object.keys(dictionary.en).sort()).toEqual(Object.keys(dictionary.ru).sort());
    expect(Object.values(dictionary.ru).every(value => value.length > 0)).toBe(true);
  }
  expect(displaySharedText('ru', 'toString')).toBe('toString');
  expect(displayHostedAgentText('ru', 'constructor')).toBe('constructor');
  expect(displaySharedText('ru', 'synthetic_private_name')).toBe('synthetic_private_name');
  expect(displayHostedAgentText('ru', 'Network error')).toBe('Ошибка сети');
  expect(displayHostedAgentText('ru', 'unknown_machine_status')).toBe('unknown_machine_status');
});
