import { expect, test } from "@playwright/test";
test("feedback retry after lost success creates one moderator queue item",async({page})=>{
 test.skip(process.env.ISOLATED_WEB_E2E!=="1"||process.env.WEB_TEST_URL!=="http://127.0.0.1:3100","Requires isolated feedback queue");
 const message=`TEST_DATA lost-response feedback ${crypto.randomUUID()}`;await page.goto("/feedback");
 await page.route("**/api/v1/feedback",async route=>{await route.fetch();await route.abort();});
 await page.getByLabel("Ваше сообщение",{exact:true}).fill(message);await page.getByRole("button",{name:"Отправить сообщение",exact:true}).click();await expect(page.locator(".form-panel [role=alert]")).toContainText("Нет связи");
 await page.unroute("**/api/v1/feedback");await page.reload();await expect(page.getByLabel("Ваше сообщение",{exact:true})).toHaveValue(message);await expect(page.getByLabel("Ваше сообщение",{exact:true})).toHaveAttribute("readonly","");await page.getByRole("button",{name:"Повторить отправку",exact:true}).click();await expect(page.locator(".form-panel [role=status]")).toContainText("Сообщение получено");
 await page.goto("/admin");await page.getByLabel("Пароль",{exact:true}).fill(process.env.ADMIN_PASSWORD!);await page.getByRole("button",{name:"Войти",exact:true}).click();await page.getByRole("tab",{name:"Обращения",exact:true}).click();await expect(page.locator(".raw-item").filter({hasText:message})).toHaveCount(1);
});
