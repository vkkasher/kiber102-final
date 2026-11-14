import { expect, test, type Page } from "@playwright/test";

const isolated = process.env.ISOLATED_WEB_E2E === "1" && process.env.WEB_TEST_URL === "http://127.0.0.1:3100";
test.beforeEach(() => { test.skip(!isolated, "Mutation acceptance requires the dedicated isolated API8100/database and web3100 fixture"); });
async function login(page: Page) {
  await page.goto("/admin");
  await page.getByLabel("Пароль", {exact:true}).fill(process.env.ADMIN_PASSWORD!);
  await page.getByRole("button",{name:"Войти",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Редакция афиши"})).toBeVisible();
}

test("admin pagination reaches beyond100 and search opens an old draft", async ({page}) => {
  await login(page);
  for(let n=0;n<5;n++) {
    await page.getByRole("button",{name:"Следующая",exact:true}).click();
    await expect(page.locator("nav[aria-label='Страницы событий модерации']")).toContainText(`Страница ${n+2}`);
    await expect(page.getByRole("button",{name:"Обновить",exact:true})).toBeEnabled();
  }
  await expect(page.locator(".admin-table tbody tr").first()).toBeVisible();
  await page.getByLabel("Найти событие",{exact:true}).fill("TEST_DATA Изолированный сеанс 000");
  await page.getByRole("button",{name:"Искать",exact:true}).click();
  await expect(page.locator(".admin-table tbody tr")).toHaveCount(1);
  await page.getByRole("button",{name:"Редактировать",exact:true}).click();
  await expect(page.locator(".admin-editor input[name=title]")).toHaveValue("TEST_DATA Изолированный сеанс 000");
});

test("manual creation verifies and publishes then preserves cancelled permanent card", async ({page,request}) => {
  const title = `TEST_DATA ручной ввод ${crypto.randomUUID().slice(0,8)}`;
  await login(page);
  await page.getByRole("button",{name:"Создать событие",exact:true}).click();
  const form = page.locator(".admin-editor");
  for(const [name,value] of Object.entries({title,category:"спектакль",city:"Уфа",venue:"TEST_DATA Только изолированная площадка",address:"TEST_DATA адрес изолированной площадки",date_start:"2027-01-20T16:00:00+05:00",source_url:`https://example.org/test/${crypto.randomUUID()}`,age_limit:"6",price_min:"100",price_max:"100",duration_min:"60"})) await form.locator(`input[name=${name}]`).fill(value);
  await form.locator("textarea[name=description]").fill("TEST_DATA описание синтетического события, только изолированная приёмка");
  const createdResponse = page.waitForResponse(r=>r.url().endsWith("/admin/events") && r.request().method()==="POST");
  await page.getByRole("button",{name:"Создать черновик",exact:true}).click();
  const response = await createdResponse; expect(response.status()).toBe(201); const event = await response.json();
  expect((await request.get(`/events/${event.id}`)).status()).toBe(404);
  await page.getByLabel("Найти событие",{exact:true}).fill(title);
  await page.getByRole("button",{name:"Искать",exact:true}).click();
  await page.getByRole("button",{name:"Редактировать",exact:true}).click();
  await page.getByLabel("Что сверено с оригиналом").fill("TEST_DATA изолированная сверка, не является проверкой реального события");
  await page.getByLabel("Автоматически проверить официальный источник").uncheck();
  await page.getByRole("button",{name:"Проверить источник",exact:true}).click();
  await expect(page.getByText("Проверка источника выполнена. Посмотрите результат перед публикацией.")).toBeVisible();
  await form.locator("select[name=status]").selectOption("published");
  await page.getByRole("button",{name:"Сохранить изменения",exact:true}).click();
  await expect(page.getByText("Изменения сохранены.")).toBeVisible();
  expect((await request.get(`/events/${event.id}`)).status()).toBe(200);
  await page.getByRole("button",{name:"Редактировать",exact:true}).click();
  await form.locator("select[name=status]").selectOption("cancelled");
  await page.getByRole("button",{name:"Сохранить изменения",exact:true}).click();
  await expect(page.getByText("Изменения сохранены.")).toBeVisible();
  await page.goto(`/events/${event.id}`);
  await expect(page.getByText("Организатор отменил событие. Карточка сохранена для справки.")).toBeVisible();
});

test("import textarea reports new draft and feedback reaches moderator queue", async ({page}) => {
  await login(page);
  await page.getByRole("tab",{name:"Импорт",exact:true}).click();
  const unique=crypto.randomUUID();
  await page.getByLabel("Содержимое",{exact:true}).fill(JSON.stringify([{title:`TEST_DATA импорт ${unique}`,category:"концерт",city:"Уфа",venue:"TEST_DATA fixture",date_start:"2027-02-01T12:00:00+05:00",source_url:`https://example.org/import/${unique}`} ]));
  await page.getByRole("button",{name:"Проверить и импортировать",exact:true}).click();
  await expect(page.getByText(/Добавлено 1, обновлено 0/)).toBeVisible();
  await page.goto("/feedback");
  await page.getByLabel("Ваше сообщение",{exact:true}).fill(`TEST_DATA обращение ${unique}`);
  await page.getByRole("button",{name:"Отправить сообщение",exact:true}).click();
  await expect(page.getByText(/Сообщение получено и передано в очередь модерации/)).toBeVisible();
  await page.goto("/admin");
  await page.getByRole("tab",{name:"Обращения",exact:true}).click();
  const row=page.locator(".raw-item").filter({hasText:unique});
  await expect(row).toBeVisible();
  await row.getByLabel("Статус обращения",{exact:true}).selectOption("reviewing");
  await expect(row.getByLabel("Статус обращения",{exact:true})).toHaveValue("reviewing");
  await page.reload();
  await page.getByRole("tab",{name:"Обращения",exact:true}).click();
  await expect(row.getByLabel("Статус обращения",{exact:true})).toHaveValue("reviewing");
});

test("agent mutation requires explicit confirmation and retains deletion capability", async ({page}) => {
  await page.goto("/agent");
  await page.getByLabel("Поиск без языковой модели").check();
  await page.getByLabel("Ваш запрос",{exact:true}).fill("Подпиши меня на события в Уфе");
  await page.getByRole("button",{name:"Найти",exact:true}).click();
  await expect(page.getByRole("region",{name:"Подтверждение действия"})).toBeVisible();
  await page.reload();
  await expect(page.getByRole("region",{name:"Подтверждение действия"})).toBeVisible();
  await page.getByRole("button",{name:"Подтвердить",exact:true}).click();
  await expect(page.getByText(/Подписка сохранена/)).toBeVisible();
  expect(await page.evaluate(()=>JSON.parse(localStorage.getItem("ayda_subscription")!).delete_token)).toBeTruthy();
  await page.goto("/");
  await page.getByRole("button",{name:"Удалить подписку",exact:true}).click();
  await expect(page.locator(".subscription [role=status]")).toHaveText("Подписка удалена.");
});
