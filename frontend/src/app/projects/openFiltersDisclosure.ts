import { fireEvent, screen } from "@testing-library/react";
import { publicMessages } from "@/lib/i18n/public";

/** Test helper: expand the Filters disclosure if it is collapsed (period and category live inside). */
export function openFiltersDisclosure(locale: "en" | "ru"): void {
  const toggle = screen.getByRole("button", { name: new RegExp(`^${publicMessages[locale].filtersLabel}`) });
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
}
