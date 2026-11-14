import { expect, test } from "@playwright/test";

test("widget configurator keeps code, preview, dates and height consistent and explains clipboard denial", async ({
  page,
}) => {
  await page.addInitScript(() =>
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async () => {
          throw new DOMException("Clipboard blocked", "NotAllowedError");
        },
      },
    }),
  );
  await page.goto("/embed");
  await page.getByLabel("Город", { exact: true }).fill("Уфа");
  await page.getByLabel("Категория", { exact: true }).fill("спектакль");
  await page.getByLabel("Период", { exact: true }).selectOption("custom");
  await page.getByLabel("С даты", { exact: true }).fill("2027-01-10");
  await page.getByLabel("По дату", { exact: true }).fill("2027-01-20");
  await page.getByLabel("Высота, px", { exact: true }).fill("100");
  const frame = page.locator('iframe[title="Предпросмотр афиши"]');
  const src = await frame.getAttribute("src");
  const url = new URL(src!);
  expect(Object.fromEntries(url.searchParams)).toEqual({
    city: "Уфа",
    category: "спектакль",
    date_from: "2027-01-10",
    date_to: "2027-01-20",
  });
  const code = page.getByRole("region", {
    name: "HTML-код виджета",
    exact: true,
  });
  await expect(code).toContainText(src!);
  await expect(code).toContainText('height="350"');
  expect(await frame.evaluate((el) => el.getBoundingClientRect().height)).toBe(
    350,
  );
  await page
    .getByRole("button", { name: "Скопировать код", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText(
    "скопируйте его вручную",
  );
  await expect(code).toBeVisible();
});
