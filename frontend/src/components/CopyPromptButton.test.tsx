// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { hydrateRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { CopyPromptButton } from "./CopyPromptButton";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

let root: Root | undefined;
afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  cleanup();
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

it.each(["en", "ru"] as const)("disables the server control until hydration, then copies in %s", async locale => {
  const text = "Connect my agent to AgentSpore.";
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  const ui = <LocaleProvider initialLocale={locale}><CopyPromptButton text={text} /></LocaleProvider>;
  const container = document.createElement("div");
  container.innerHTML = renderToString(ui);
  document.body.append(container);
  const button = screen.getByRole<HTMLButtonElement>("button", { name: docsMessages[locale].copyPrompt });
  const earlyActivation = vi.fn();
  button.addEventListener("click", earlyActivation);
  expect(button.disabled).toBe(true);
  button.focus();
  button.click();
  expect(document.activeElement).not.toBe(button);
  expect(earlyActivation).not.toHaveBeenCalled();
  expect(writeText).not.toHaveBeenCalled();
  expect(screen.getByRole("status").textContent).toBe("");
  button.removeEventListener("click", earlyActivation);

  const onRecoverableError = vi.fn();
  await act(async () => { root = hydrateRoot(container, ui, { onRecoverableError }); });
  expect(screen.getByRole("button")).toBe(button);
  expect(button.disabled).toBe(false);
  expect(button.type).toBe("button");
  button.focus();
  expect(document.activeElement).toBe(button);
  await act(async () => button.click());
  expect(writeText).toHaveBeenCalledExactlyOnceWith(text);
  expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
  expect(screen.getByRole("status").textContent).toBe(docsMessages[locale].promptCopied);
  expect(onRecoverableError).not.toHaveBeenCalled();
});

it.each(["en", "ru"] as const)("announces clipboard denial after hydration in %s", async locale => {
  const writeText = vi.fn().mockRejectedValue(new DOMException("Permission denied", "NotAllowedError"));
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  const ui = <LocaleProvider initialLocale={locale}><CopyPromptButton text="Agent prompt" /></LocaleProvider>;
  const container = document.createElement("div");
  container.innerHTML = renderToString(ui);
  document.body.append(container);
  await act(async () => { root = hydrateRoot(container, ui); });
  await act(async () => fireEvent.click(screen.getByRole("button")));
  expect(writeText).toHaveBeenCalledExactlyOnceWith("Agent prompt");
  expect(screen.getByRole("status").textContent).toBe(docsMessages[locale].promptCopyFailed);
  expect(screen.getByRole<HTMLButtonElement>("button").disabled).toBe(false);
});
