import { expect, test } from "@playwright/test";

const isolated =
  process.env.ISOLATED_WEB_E2E === "1" &&
  process.env.WEB_TEST_URL === "http://127.0.0.1:3100";
test.beforeEach(() =>
  test.skip(
    !isolated,
    "Import writes require the isolated API8100 and web3100",
  ),
);

function windows1251(text: string) {
  return Buffer.from(
    [...text].map((character) => {
      const code = character.charCodeAt(0);
      if (code < 128) return code;
      if (code >= 0x410 && code <= 0x44f) return code - 0x410 + 0xc0;
      throw new Error("Unsupported TEST_DATA fixture character");
    }),
  );
}

test("Russian Excel upload preserves quotes, stays a draft and repeated import has no duplicate", async ({
  page,
  request,
}, info) => {
  const publicBefore = (await (await request.get("/api/v1/events")).json()).total;
  await page.goto("/admin");
  await page
    .getByLabel("Пароль", { exact: true })
    .fill(process.env.ADMIN_PASSWORD!);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Редакция афиши" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Импорт", exact: true }).click();
  const title = `TEST_DATA ${crypto.randomUUID().slice(0, 8)} "Куклы"`;
  const date = new Date(Date.now() + 7 * 86400_000).toISOString();
  const content =
    "title;category;city;venue;address;description;date_start;source_url\n" +
    `"${title.replaceAll('"', '""')}";спектакль;Уфа;TEST_DATA Зал;TEST_DATA Адрес;TEST_DATA Описание;${date};https://example.org/TEST_DATA/${crypto.randomUUID()}\n`;
  await page
    .getByLabel("Файл JSON или CSV")
    .setInputFiles({
      name: "afisha.csv",
      mimeType: "text/csv",
      buffer: windows1251(content),
    });
  await expect(
    page.getByRole("textbox", { name: "Содержимое", exact: true }),
  ).toHaveValue(content);
  await expect(
    page.getByText(/Загружен файл: afisha.csv.*Windows-1251/),
  ).toBeVisible();
  await page.getByRole("button", { name: "Проверить и импортировать" }).click();
  await expect(
    page.getByRole("heading", { name: "Импорт завершён" }),
  ).toBeVisible();
  await expect(
    page
      .locator(".notice")
      .filter({ has: page.getByRole("heading", { name: "Импорт завершён" }) }),
  ).toContainText("Добавлено 1");
  expect((await (await request.get("/api/v1/events")).json()).total).toBe(publicBefore);
  await page.getByRole("button", { name: "Проверить и импортировать" }).click();
  await expect(
    page
      .locator(".notice")
      .filter({ has: page.getByRole("heading", { name: "Импорт завершён" }) }),
  ).toContainText("дублей 1");
  await page.screenshot({
    path: info.outputPath("excel-import.png"),
    fullPage: true,
  });
  const previous = await page
    .getByRole("textbox", { name: "Содержимое", exact: true })
    .inputValue();
  await page
    .getByLabel("Файл JSON или CSV")
    .setInputFiles({
      name: "broken.json",
      mimeType: "application/json",
      buffer: Buffer.from([0xd3, 0xf4, 0xe0]),
    });
  await expect(page.getByRole("alert").filter({ hasText: "UTF-8" })).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Содержимое", exact: true }),
  ).toHaveValue(previous);
  await page.getByRole("tab", { name: "События", exact: true }).click();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
});
