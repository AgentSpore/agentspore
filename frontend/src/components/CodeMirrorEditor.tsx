"use client";

import { useEffect, useRef, useCallback } from "react";
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { defaultKeymap, indentWithTab } from "@codemirror/commands";
import { LanguageSupport } from "@codemirror/language";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { yaml } from "@codemirror/lang-yaml";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { oneDark } from "@codemirror/theme-one-dark";

const LANG_MAP: Record<string, () => LanguageSupport> = {
  py: python,
  python: python,
  js: () => javascript(),
  jsx: () => javascript({ jsx: true }),
  ts: () => javascript({ typescript: true }),
  tsx: () => javascript({ jsx: true, typescript: true }),
  json: json,
  md: markdown,
  markdown: markdown,
  yaml: yaml,
  yml: yaml,
  html: html,
  htm: html,
  css: css,
  toml: yaml, // close enough for highlighting
  cfg: yaml,
  ini: yaml,
};

function getLang(filePath: string): LanguageSupport | null {
  const ext = filePath.split(".").pop()?.toLowerCase() || "";
  const factory = LANG_MAP[ext];
  return factory ? factory() : null;
}

const agentSporeTheme = EditorView.theme({
  "&": {
    backgroundColor: "transparent",
    color: "#d4d4d8",
    fontSize: "12px",
    fontFamily: "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace",
    height: "100%",
  },
  ".cm-content": {
    padding: "8px 0",
    caretColor: "#a78bfa",
  },
  ".cm-cursor": {
    borderLeftColor: "#a78bfa",
  },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": {
    backgroundColor: "rgba(139, 92, 246, 0.2) !important",
  },
  ".cm-gutters": {
    backgroundColor: "transparent",
    color: "#525252",
    border: "none",
    borderRight: "1px solid rgba(82, 82, 82, 0.3)",
    minWidth: "40px",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "transparent",
    color: "#a3a3a3",
  },
  ".cm-activeLine": {
    backgroundColor: "rgba(255, 255, 255, 0.02)",
  },
  ".cm-line": {
    padding: "0 12px",
  },
  "&.cm-focused": {
    outline: "none",
  },
  ".cm-scroller": {
    overflow: "auto",
    lineHeight: "18px",
  },
});

interface CodeMirrorEditorProps {
  value: string;
  onChange: (value: string) => void;
  onSave?: () => void;
  filePath: string;
  readOnly?: boolean;
}

export default function CodeMirrorEditor({ value, onChange, onSave, filePath, readOnly }: CodeMirrorEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);

  onChangeRef.current = onChange;
  onSaveRef.current = onSave;

  const saveKeymap = useCallback(() => {
    return keymap.of([{
      key: "Mod-s",
      run: () => { onSaveRef.current?.(); return true; },
    }]);
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;

    const lang = getLang(filePath);
    const extensions = [
      lineNumbers(),
      highlightActiveLine(),
      highlightActiveLineGutter(),
      keymap.of([...defaultKeymap, indentWithTab]),
      saveKeymap(),
      oneDark,
      agentSporeTheme,
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          onChangeRef.current(update.state.doc.toString());
        }
      }),
      EditorState.tabSize.of(2),
      EditorView.lineWrapping,
    ];
    if (lang) extensions.push(lang);
    if (readOnly) extensions.push(EditorState.readOnly.of(true));

    const state = EditorState.create({ doc: value, extensions });
    const view = new EditorView({ state, parent: containerRef.current });
    viewRef.current = view;

    return () => { view.destroy(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filePath, readOnly]);

  // Sync external value changes (e.g. after file load)
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current !== value) {
      view.dispatch({
        changes: { from: 0, to: current.length, insert: value },
      });
    }
  }, [value]);

  return <div ref={containerRef} className="flex-1 min-h-0 overflow-hidden" />;
}
