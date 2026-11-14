import { expect, test } from "@playwright/test";

test("subscription retry through the web proxy preserves its deletion capability", async ({
  request,
}) => {
  const key = `test-retry-${crypto.randomUUID()}`;
  const options = {
    data: { city: "Уфа" },
    headers: { "Idempotency-Key": key },
  };
  const first = await request.post("/api/v1/subscriptions", options);
  expect(first.status()).toBe(201);
  const subscription = await first.json();
  try {
    const retry = await request.post("/api/v1/subscriptions", options);
    expect(retry.status()).toBe(201);
    expect(await retry.json()).toEqual(subscription);
  } finally {
    await request.delete(`/api/v1/subscriptions/${subscription.id}`, {
      headers: { Authorization: `Bearer ${subscription.delete_token}` },
    });
  }
});

test("event metadata describes the actual public event", async ({
  request,
}) => {
  const listing = await request.get("/api/v1/events?page_size=1");
  const event = (await listing.json()).items[0];
  expect(event).toBeTruthy();
  const document = await request.get(`/events/${event.id}`);
  const html = await document.text();
  expect(html).toContain(`property="og:title" content="${event.title}`);
});

test("catalog recovers from a failed API request without fabricated results", async ({
  page,
}) => {
  await page.route("**/api/v1/events?*", (route) => route.abort());
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Афиша событий" }),
  ).toBeVisible();
  await expect(
    page.getByText("Не удалось загрузить данные", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".event-card")).toHaveCount(0);
  await page.unroute("**/api/v1/events?*");
  await page.getByRole("button", { name: "Повторить загрузку" }).click();
  await expect(page.getByText(/событий по вашему запросу/)).toBeVisible();
  await expect(page.locator(".event-card").first()).toBeVisible();
});

test("empty search explains recovery and reset restores actual catalog", async ({
  page,
}) => {
  await page.goto("/?q=nonexistent-qa-result-85375931");
  await expect(
    page.getByRole("heading", { name: "Пока ничего не нашлось" }),
  ).toBeVisible();
  await expect(page.locator(".event-card")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Сбросить фильтры", exact: true })
    .click();
  await expect(page.locator(".event-card").first()).toBeVisible();
});

test("catalog renders without overflow at shipping widths and exposes honest result state", async ({
  page,
}) => {
  for (const width of [360, 390, 768, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Культура, которая рядом" }),
    ).toBeVisible();
    await expect(page.getByText(/событий по вашему запросу/)).toBeVisible();
    await expect(
      page.locator(".event-card").first().or(page.locator(".empty-state")),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
});

test("combined filters persist in URL and browser back restores query", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Все фильтры" }).click();
  await page
    .getByRole("combobox", { name: "Категория", exact: true })
    .selectOption("спектакль");
  await page.getByLabel("Цена до, ₽").fill("900");
  await expect(page).toHaveURL(/price_max=900/);
  await expect(page).toHaveURL(/category=/);
  await page.reload();
  await page.getByRole("button", { name: "Все фильтры" }).click();
  await expect(page.getByLabel("Цена до, ₽")).toHaveValue("900");
  await page.getByRole("button", { name: "Сбросить всё", exact: true }).click();
  await expect(page).toHaveURL((process.env.WEB_TEST_URL || "http://127.0.0.1:3000") + "/");
  await page.goBack();
  await expect(page.getByLabel("Цена до, ₽")).toHaveValue("900");
});

test("real agent API answers a request without inventing client cards", async ({
  page,
}) => {
  await page.goto("/agent");
  await page.getByLabel("Поиск без языковой модели").check();
  await page.getByLabel("Ваш запрос").fill("Куда сходить в Уфе бесплатно?");
  await page.getByRole("button", { name: "Найти", exact: true }).click();
  await expect(page.locator(".chat-message.assistant")).toBeVisible();
  await expect(
    page.getByText("Ответ без языковой модели. Факты из каталога."),
  ).toBeVisible();
});

test("iframe uses live catalog and has no horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/embed");
  await page.getByLabel("Город", { exact: true }).fill("Уфа");
  const frame = page.frameLocator("iframe");
  await expect(
    frame.getByRole("heading", { name: "Афиша событий" }),
  ).toBeVisible();
  await expect(frame.getByText(/событий по вашему запросу/)).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("admin rejects invalid password and has no indexing permission", async ({
  page,
}) => {
  await page.goto("/admin");
  await expect(page.locator("meta[name=robots]")).toHaveAttribute(
    "content",
    /noindex/,
  );
  await page.getByLabel("Пароль").fill("invalid-e2e-password");
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.locator(".login-form [role=alert]")).toContainText(
    "Неверный пароль",
  );
});

test("keyboard skip link goes directly to content", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "К содержимому" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#content/);
});

test("real event card exposes source links without technical timestamps", async ({
  page,
}) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const link = page.locator(".event-card h3 a").first();
  await expect(link).toBeVisible();
  const title = await link.innerText();
  await link.click();
  await expect(
    page.getByRole("heading", { name: title, exact: true, level: 1 }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Откуда информация" }),
  ).toBeVisible();
  const source = page.locator(".source-block a.source-link").first();
  await expect(source).toHaveAttribute("href", /^https:\/\//);
  await expect(source).toHaveAttribute("rel", /noopener/);
  await expect(page.locator(".source-block")).not.toContainText(
    "Последняя проверка",
  );
});

test("admin can inspect real events, audit and traces with configured credentials", async ({
  page,
}) => {
  test.skip(
    !process.env.ADMIN_PASSWORD,
    "Set ADMIN_PASSWORD to verify the authenticated UI against your local API",
  );
  await page.goto("/admin");
  await page.getByLabel("Пароль").fill(process.env.ADMIN_PASSWORD!);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Редакция афиши" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Редактировать" }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Редактировать" }).first().click();
  await expect(
    page.getByRole("heading", { name: "Проверка источника" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Журнал", exact: true }).click();
  await expect(page.locator(".raw-item").first()).toBeVisible();
  await page.getByRole("tab", { name: "Трассы агента", exact: true }).click();
  await expect(page.locator(".raw-item").first()).toBeVisible();
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Вход для модератора" }),
  ).toBeVisible();
});
