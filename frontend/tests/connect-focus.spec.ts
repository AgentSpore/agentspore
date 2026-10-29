import { expect, test, type Page } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3000";
test.use({ baseURL });

const longCopy = {
  en: "Check that X-API-Key is sent correctly. A 401 does not prove that the agent identity is missing. Ask the operator how to recover access to the existing agent. Do not register another agent automatically. If recovery is unavailable, setup remains incomplete. Never post keys or personal email addresses in public chat.",
  ru: "Вместе с оператором проверьте передачу X-API-Key и возможность восстановить доступ к существующему агенту. Ответ 401 не доказывает, что агента нет. Не запускайте повторную регистрацию автоматически. Если восстановление недоступно, подключение остаётся незавершённым. Не публикуйте ключи и личные email-адреса в открытом чате.",
};

async function openGuide(page: Page, locale: "en" | "ru", width: number, height = 844) {
  await page.setViewportSize({ width, height });
  await page.context().addCookies([{ name: "agentspore_locale", value: locale, url: baseURL }]);
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin === new URL(baseURL).origin && !url.pathname.startsWith("/api/")) {
      await route.continue();
    } else if (url.origin === "https://github.com") {
      await route.fulfill({ status: 200, contentType: "text/html", body: "Synthetic external destination" });
    } else {
      await route.abort();
    }
  });
  await page.goto("/docs/connect", { waitUntil: "networkidle" });
}

async function focusTable(page: Page) {
  for (let i = 0; i < 30; i++) {
    await page.keyboard.press("Tab");
    if (await page.locator("#errors + div").evaluate(el => document.activeElement === el)) return true;
  }
  return false;
}

async function geometry(page: Page) {
  return page.locator("#errors + div").evaluate(el => {
    const header = document.querySelector("header");
    const row = el.querySelector("tbody tr");
    if (!header || !row) throw new Error("Missing table or header");
    return {
      focused: document.activeElement === el,
      headerBottom: header.getBoundingClientRect().bottom,
      tableTop: el.getBoundingClientRect().top,
      height: el.getBoundingClientRect().height,
      rowTop: row.getBoundingClientRect().top,
      horizontal: el.scrollWidth > el.clientWidth,
      vertical: el.scrollHeight > el.clientHeight,
      left: el.scrollLeft,
      contentHeight: el.scrollHeight,
    };
  });
}

async function observeKeys(page: Page) {
  await page.evaluate(() => {
    document.addEventListener("keydown", event => {
      document.documentElement.dataset.keyPrevented = String(event.defaultPrevented);
    });
  });
}

for (const locale of ["en", "ru"] as const) {
  for (const variant of ["current", "long"] as const) {
    for (const width of [390, 1440]) {
      test(`${locale} ${variant} ${width}: keyboard focus and scroll`, async ({ page }) => {
        await openGuide(page, locale, width);
        if (variant === "long") {
          await page.locator("#errors + div tbody tr:first-child td:last-child").evaluate((cell, text) => {
            cell.textContent = text;
          }, longCopy[locale]);
        }
        const found = await focusTable(page);
        expect(found).toBe(true);
        await expect(page.getByRole("region", { name: locale === "en" ? "Common errors table" : "Таблица частых ошибок" })).toBeFocused();
        const forward = await geometry(page);
        expect(forward.tableTop).toBeGreaterThanOrEqual(forward.headerBottom);
        expect(forward.rowTop).toBeGreaterThanOrEqual(forward.headerBottom);
        if (width === 390) {
          expect(forward.contentHeight).toBeGreaterThan(844);
          expect(forward.vertical).toBe(true);
        } else {
          expect(forward.horizontal).toBe(false);
          expect(forward.vertical).toBe(false);
        }
        if (locale === "ru" && width === 390) {
          expect(forward.horizontal).toBe(true);
          await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
          await page.keyboard.press("ArrowRight");
          await expect.poll(async () => (await geometry(page)).left).toBeGreaterThan(0);
        }
        await page.keyboard.press("Tab");
        await page.keyboard.press("Shift+Tab");
        await expect.poll(async () => {
          const current = await geometry(page);
          return current.focused && current.tableTop >= current.headerBottom && current.rowTop >= current.headerBottom;
        }).toBe(true);
        const reverse = await geometry(page);
        expect(reverse.focused).toBe(true);
        expect(reverse.tableTop).toBeGreaterThanOrEqual(reverse.headerBottom);
        expect(reverse.rowTop).toBeGreaterThanOrEqual(reverse.headerBottom);
      });
    }
  }
  test(`${locale}: links leave the page and clear its scroll padding`, async ({ page }) => {
    await openGuide(page, locale, 390);
    await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).scrollPaddingTop)).toBe("96px");
    await page.locator('main a[href="/docs/glossary"]').click();
    await expect(page).toHaveURL(/\/docs\/glossary$/);
    await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).scrollPaddingTop)).toBe("auto");
    await page.goto("/docs/connect", { waitUntil: "networkidle" });
    await page.locator('footer a[href="https://github.com/AgentSpore/agentspore"]').click();
    await expect(page).toHaveURL("https://github.com/AgentSpore/agentspore");
    await expect(page.getByText("Synthetic external destination")).toBeVisible();
  });
}

test("horizontal keys are not intercepted for modifiers or PageDown", async ({ page }) => {
  await openGuide(page, "ru", 390);
  expect(await focusTable(page)).toBe(true);
  await observeKeys(page);
  for (const key of ["Shift+ArrowRight", "Alt+ArrowRight", "Control+ArrowRight", "Meta+ArrowRight", "PageDown"]) {
    await page.keyboard.press(key);
    await expect(page.locator("html")).toHaveAttribute("data-key-prevented", "false");
  }
});

test("horizontal keys are not intercepted when the region does not overflow", async ({ page }) => {
  await openGuide(page, "en", 1440);
  expect(await focusTable(page)).toBe(true);
  expect((await geometry(page)).horizontal).toBe(false);
  await observeKeys(page);
  await page.keyboard.press("ArrowRight");
  await expect(page.locator("html")).toHaveAttribute("data-key-prevented", "false");
});

test("horizontal keys are not intercepted for a focused child", async ({ page }) => {
  await openGuide(page, "ru", 390);
  const region = page.locator("#errors + div");
  await region.evaluate(el => {
    const button = document.createElement("button");
    button.textContent = "Synthetic child";
    el.append(button);
  });
  await page.getByRole("button", { name: "Synthetic child" }).focus();
  await observeKeys(page);
  await page.keyboard.press("ArrowRight");
  await expect(page.locator("html")).toHaveAttribute("data-key-prevented", "false");
});

test("RTL horizontal keys follow physical directions", async ({ page }) => {
  await openGuide(page, "ru", 390);
  const region = page.locator("#errors + div");
  await region.evaluate(el => { el.setAttribute("dir", "rtl"); el.scrollLeft = 0; });
  expect(await focusTable(page)).toBe(true);
  await page.keyboard.press("ArrowLeft");
  await expect.poll(async () => (await geometry(page)).left).toBeLessThan(0);
  const left = (await geometry(page)).left;
  await page.keyboard.press("ArrowRight");
  await expect.poll(async () => (await geometry(page)).left).toBeGreaterThan(left);
});

test("keyboard focus and horizontal scroll work with reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openGuide(page, "ru", 390);
  expect(await focusTable(page)).toBe(true);
  const current = await geometry(page);
  expect(current.tableTop).toBeGreaterThanOrEqual(current.headerBottom);
  await page.keyboard.press("ArrowRight");
  await expect.poll(async () => (await geometry(page)).left).toBeGreaterThan(0);
});

for (const size of [{ width: 390, height: 160 }, { width: 195, height: 100 }, { width: 98, height: 211 }, { width: 1440, height: 160 }]) {
  test(`small viewport ${size.width}x${size.height} keeps the first row visible`, async ({ page }) => {
    await openGuide(page, "ru", size.width, size.height);
    expect(await focusTable(page)).toBe(true);
    await expect.poll(async () => {
      const current = await geometry(page);
      return current.focused && current.height >= 64 && current.rowTop >= Math.max(0, current.headerBottom) && current.rowTop < size.height;
    }).toBe(true);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab");
    await expect.poll(async () => {
      const current = await geometry(page);
      return current.focused && current.rowTop >= Math.max(0, current.headerBottom) && current.rowTop < size.height;
    }).toBe(true);
  });
}
