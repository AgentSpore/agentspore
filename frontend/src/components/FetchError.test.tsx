// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FetchError } from "./FetchError";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { sharedMessages } from "@/lib/i18n/shared";

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
afterEach(cleanup);

it.each(["en", "ru"] as const)("announces the failure and retries on click in %s", locale => {
  const onRetry = vi.fn();
  render(<LocaleProvider initialLocale={locale}><FetchError onRetry={onRetry} /></LocaleProvider>);
  expect(screen.getByRole("alert").textContent).toContain(sharedMessages[locale].couldNotLoadData);
  fireEvent.click(screen.getByRole("button", { name: sharedMessages[locale].retry }));
  expect(onRetry).toHaveBeenCalledTimes(1);
});
