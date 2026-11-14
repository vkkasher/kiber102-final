import { expect, test } from "@playwright/test";
test("planner form sends interests/count and shows a factual two-event group total",async({page})=>{
 test.skip(process.env.ISOLATED_WEB_E2E!=="1"||process.env.WEB_TEST_URL!=="http://127.0.0.1:3100","Requires isolated priced/duration fixture");
 await page.goto("/agent");await page.getByText("Собрать выходной день",{exact:true}).click();const planner=page.locator("details.planner");
 await planner.getByLabel("Город",{exact:true}).fill("Уфа");await planner.getByLabel("Дата",{exact:true}).fill("2027-01-13");await planner.getByLabel("Общий бюджет, ₽",{exact:true}).fill("400");await planner.getByLabel("Взрослых",{exact:true}).fill("2");await planner.getByLabel("Направление",{exact:true}).selectOption("спектакль");await planner.getByLabel("Количество событий",{exact:true}).selectOption("2");
 const responsePromise=page.waitForResponse(r=>r.url().endsWith("/agent/plan"));await planner.getByRole("button",{name:"Составить план",exact:true}).click();const response=await responsePromise;
 expect(response.status()).toBe(200);expect(response.request().postDataJSON()).toMatchObject({interests:["спектакль"],event_count:2,adults:2,budget:400});
 const result=await response.json();expect(result.feasible).toBe(true);expect(result.events).toHaveLength(2);expect(result.total_max).toBe(400);expect(result.travel_status).toBe("same_venue");
 await expect(planner.getByText("План найден",{exact:true})).toBeVisible();await expect(planner.locator(".planner-result .event-card")).toHaveCount(2);await expect(planner.getByText(/Стоимость для всей компании: 400/)).toBeVisible();await expect(planner.getByText(/События на одной площадке/)).toBeVisible();
});
