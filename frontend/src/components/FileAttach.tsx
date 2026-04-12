"use client";

import { useRef } from "react";

const TEXT_EXTS = new Set([
  "txt", "md", "py", "js", "ts", "tsx", "jsx", "json", "csv", "yaml", "yml",
  "toml", "xml", "html", "css", "scss", "sql", "sh", "bash", "zsh", "log",
  "env", "cfg", "ini", "conf", "go", "rs", "java", "kt", "rb", "php", "c",
  "cpp", "h", "hpp", "swift", "dart", "r", "pl", "lua", "dockerfile", "makefile",
]);
const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"]);
const MAX_TEXT_SIZE = 500_000; // 500KB
const MAX_IMAGE_SIZE = 2_000_000; // 2MB

export type FileData = {
  name: string;
  type: "text" | "image";
  content: string; // text content or base64 data URL
  size: number;
};

type Props = {
  onFile: (file: FileData) => void;
  disabled?: boolean;
};

function getExt(name: string): string {
  const parts = name.toLowerCase().split(".");
  return parts.length > 1 ? parts.pop()! : "";
}

export function FileAttach({ onFile, disabled }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    // Reset so same file can be re-selected
    e.target.value = "";

    const ext = getExt(file.name);

    if (TEXT_EXTS.has(ext) || file.type.startsWith("text/")) {
      if (file.size > MAX_TEXT_SIZE) {
        alert(`File too large (${(file.size / 1024).toFixed(0)}KB). Max ${MAX_TEXT_SIZE / 1024}KB for text files.`);
        return;
      }
      const content = await file.text();
      onFile({ name: file.name, type: "text", content, size: file.size });
    } else if (IMAGE_EXTS.has(ext) || file.type.startsWith("image/")) {
      if (file.size > MAX_IMAGE_SIZE) {
        alert(`Image too large (${(file.size / 1024 / 1024).toFixed(1)}MB). Max ${MAX_IMAGE_SIZE / 1024 / 1024}MB.`);
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        onFile({ name: file.name, type: "image", content: reader.result as string, size: file.size });
      };
      reader.readAsDataURL(file);
    } else {
      alert(`Unsupported file type (.${ext}). Supported: text/code files and images.`);
    }
  };

  return (
    <>
      <input ref={inputRef} type="file" className="hidden" onChange={handleChange} />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        title="Attach file"
        className="shrink-0 w-10 h-10 rounded-lg flex items-center justify-center bg-neutral-900 border border-neutral-800 text-neutral-500 hover:text-white hover:border-neutral-700 transition disabled:opacity-30"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
        </svg>
      </button>
    </>
  );
}
