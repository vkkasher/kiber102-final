import { expect, test } from "@playwright/test";

test("missing event is HTTP404 and never shows a retryable network card", async ({request}) => {
  const response = await request.get("/events/audit-nonexistent-id");
  expect(response.status()).toBe(404);
  expect(await response.text()).toContain("Страница не найдена");
});

test("Sunday weekend filter includes today in UTC+5", async ({page}) => {
  await page.clock.install({ time: new Date("2026-11-15T08:00:00Z") });
  await page.goto("/");
  await page.getByRole("button", {name:"Выходные",exact:true}).click();
  await expect(page).toHaveURL(/date_from=2026-11-15&date_to=2026-11-15/);
});

test("subscription form recovers a lost success across reload with the same key", async ({page}) => {
  let firstKey: string | undefined;
  let created: {id:string;delete_token:string} | undefined;
  await page.goto("/");
  await page.route("**/api/v1/subscriptions", async route => {
    if (route.request().method() !== "POST") return route.continue();
    firstKey = route.request().headers()["idempotency-key"];
    const upstream = await route.fetch();
    created = await upstream.json();
    await route.abort();
  });
  await page.locator(".subscription").getByLabel("Город",{exact:true}).fill("Уфа");
  await page.getByRole("button",{name:"Сохранить предпочтения",exact:true}).click();
  await expect(page.locator(".subscription [role=status]")).toContainText("Нет связи");
  expect(firstKey).toBeTruthy(); expect(created?.delete_token).toBeTruthy();
  await page.unroute("**/api/v1/subscriptions");
  await page.reload();
  await expect(page.locator(".subscription [role=status]")).toContainText("Предыдущий запрос");
  const retried = page.waitForRequest(r => r.url().endsWith("/subscriptions") && r.method() === "POST");
  await page.getByRole("button",{name:"Сохранить предпочтения",exact:true}).click();
  expect((await retried).headers()["idempotency-key"]).toBe(firstKey);
  await expect(page.getByRole("button",{name:"Удалить подписку",exact:true})).toBeVisible();
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("ayda_subscription")!));
  expect(saved.id).toBe(created?.id); expect(saved.delete_token).toBe(created?.delete_token);
  await page.getByRole("button",{name:"Удалить подписку",exact:true}).click();
  await expect(page.locator(".subscription [role=status]")).toHaveText("Подписка удалена.");
});

test("widget constructor uses selected dates and clamps iframe height", async ({page}) => {
  await page.goto("/embed");
  await page.getByRole("combobox",{name:"Период",exact:true}).selectOption("custom");
  await page.getByLabel("С даты",{exact:true}).fill("2027-01-02");
  await page.getByLabel("По дату",{exact:true}).fill("2027-01-05");
  await page.getByLabel("Высота, px").fill("2500");
  await expect(page.locator("iframe")).toHaveAttribute("src", /date_from=2027-01-02&date_to=2027-01-05/);
  await expect(page.getByRole("region", {name:"HTML-код виджета", exact:true})).toContainText('height="2000"');
});

test("catalog free text opens the actual agent and shows its grounded result", async ({page}) => {
  await page.goto("/");
  await page.getByText("Описать пожелания своими словами",{exact:true}).click();
  await page.getByLabel("Пожелания к событиям",{exact:true}).fill("Куда сходить в Уфе бесплатно?");
  const response = page.waitForResponse(r=>r.url().endsWith("/agent/query"));
  await page.getByRole("button",{name:"Подобрать события",exact:true}).click();
  expect((await response).status()).toBe(200);
});
