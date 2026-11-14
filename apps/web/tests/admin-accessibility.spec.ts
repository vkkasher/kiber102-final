import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
const isolated=process.env.ISOLATED_WEB_E2E==="1"&&process.env.WEB_TEST_URL==="http://127.0.0.1:3100";
test.beforeEach(()=>test.skip(!isolated,"Requires dedicated loopback fixture with admin/moderator/viewer accounts"));

test("admin tabs support arrows/Home/End and viewer cannot inherit a privileged panel",async({page})=>{
  await page.goto("/admin");await page.getByLabel("Пароль",{exact:true}).fill(process.env.ADMIN_PASSWORD!);await page.getByRole("button",{name:"Войти",exact:true}).click();
  await page.getByRole("tab",{name:"События",exact:true}).focus();await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab",{name:"Импорт",exact:true})).toBeFocused();await expect(page.getByRole("tabpanel",{name:"Импорт",exact:true})).toBeVisible();
  await page.keyboard.press("End");await expect(page.getByRole("tab",{name:"Метрики",exact:true})).toBeFocused();
  await page.keyboard.press("Home");await expect(page.getByRole("tab",{name:"События",exact:true})).toBeFocused();
  await page.getByRole("tab",{name:"Импорт",exact:true}).click();await page.getByRole("button",{name:"Выйти",exact:true}).click();
  await page.getByLabel("Учётная запись, если настроена").fill("viewer");await page.getByLabel("Пароль",{exact:true}).fill(process.env.ADMIN_PASSWORD!);await page.getByRole("button",{name:"Войти",exact:true}).click();
  await expect(page.getByText("Роль: Просмотр",{exact:true})).toBeVisible();
  await expect(page.getByRole("tab",{name:"События",exact:true})).toHaveAttribute("aria-selected","true");
  await expect(page.getByRole("tab",{name:"Импорт",exact:true})).toHaveCount(0);
  await expect(page.getByRole("button",{name:"Проверить и импортировать",exact:true})).toHaveCount(0);
  await expect(page.getByRole("button",{name:"Создать событие",exact:true})).toHaveCount(0);
  await expect(page.getByRole("button",{name:"Редактировать",exact:true}).first()).toBeDisabled();
});

for(const width of[360,390,768,1280])test(`all seven admin panels and manual form accessibility at ${width}px`,async({page},info)=>{
  // Seven full axe scans share one test budget on smaller CI machines.
  test.setTimeout(90_000);
  await page.setViewportSize({width,height:900});
  await page.request.post("/api/v1/agent/query",{data:{message:"События в Уфе",offline:true}});
  await page.request.post("/api/v1/feedback",{data:{message:"TEST_DATA проверка доступности очереди"}});
  await page.goto("/admin");await page.getByLabel("Пароль",{exact:true}).fill(process.env.ADMIN_PASSWORD!);await page.getByRole("button",{name:"Войти",exact:true}).click();
  for(const tab of["События","Импорт","Источники","Обращения","Журнал","Трассы агента","Метрики"]){
    await page.getByRole("tab",{name:tab,exact:true}).click();await expect(page.getByRole("tabpanel",{name:tab,exact:true})).toBeVisible();
    if(tab==="События")await page.getByRole("button",{name:"Создать событие",exact:true}).click();
    const result=await new AxeBuilder({page}).withTags(["wcag2a","wcag2aa","wcag21aa","best-practice"]).analyze();
    await info.attach(`admin-${width}-${tab}`,{body:JSON.stringify(result.violations,null,2),contentType:"application/json"});expect(result.violations,`${width}px ${tab}`).toEqual([]);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
  await page.getByRole("tab",{name:"Журнал",exact:true}).click();
  const raw=page.getByRole("textbox",{name:"Данные JSON записи 1",exact:true});await expect(raw).toHaveAttribute("readonly","");await raw.focus();await expect(raw).toBeFocused();
});
