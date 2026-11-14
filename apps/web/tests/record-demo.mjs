/* global document */
/** Read-only browser reserve recording. Defaults to five minutes of the actual local catalog. */
import { chromium, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const args = process.argv.slice(2);
function option(name, fallback) {
  const index = args.indexOf(name);
  return index === -1 ? fallback : args[index + 1];
}
const base = new URL(option("--url", "http://127.0.0.1:3000"));
if (!["http:", "https:"].includes(base.protocol) || base.username || base.password) throw new Error("Use an HTTP(S) catalog URL without credentials");
const duration = Number(option("--duration", "300"));
// Keep five seconds of genuine extra capture for encoder startup/navigation overhead.
const captureDuration = duration + 5;
if (!Number.isFinite(duration) || duration < 30 || duration > 3600) throw new Error("--duration must be 30..3600 seconds (default 300)");
const project = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const output = resolve(option("--output", resolve(project, `.local/demo-${stamp}.webm`)));
await mkdir(dirname(output), { recursive: true });
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", headless: true });
const context = await browser.newContext({ baseURL: base.origin, viewport: { width: 1280, height: 800 }, recordVideo: { dir: resolve(dirname(output), ".recording"), size: { width: 1280, height: 800 } } });
const info = { url: base.origin, requested_duration_seconds: duration, capture_target_seconds: captureDuration, viewport: { width:1280,height:800 }, kind: "actual-browser-read-only", telegram: "not recorded", started_at: new Date().toISOString(), steps: [], status: "recording" };
let page, video, started;
let failure;
try {
  const catalog = await context.request.get("/api/v1/events?page_size=100");
  if (!catalog.ok()) throw new Error(`Catalog unavailable: HTTP${catalog.status()}`);
  const listing = await catalog.json();
  if (!listing.items.length) throw new Error("The actual public catalog has no published sessions; no invented results will be recorded");
  if (listing.items.some(event => /TEST_DATA/.test(event.title))) throw new Error("Refusing to record synthetic fixture data as the actual catalog");
  const planInput = {city:"Стерлитамак",date:"2026-11-22",window_start:"10:00",window_end:"21:00",budget:6400,adults:2,children_ages:[6,10],max_travel_minutes:20,event_count:2};
  const planPreflight=await context.request.post("/api/v1/agent/plan",{data:planInput});
  if(!planPreflight.ok()) throw new Error("Family plan API is unavailable");
  const verifiedPlan=await planPreflight.json();
  if(!verifiedPlan.feasible || verifiedPlan.events.length!==2 || verifiedPlan.total_min!==3400 || verifiedPlan.total_max!==6400 || verifiedPlan.travel_status!=="same_venue") throw new Error("The expected factual family plan is no longer available; refusing to record invented success");
  const event = verifiedPlan.events[0];
  info.plan_input=planInput;
  info.catalog_total = listing.total;
  info.event_id = event.id;
  info.event_source = event.source_url;
  page = await context.newPage(); video = page.video(); started = Date.now();
  let step = 0;
  async function checkpoint(label, action) {
    step++;
    console.log(`${step}/20 ${label}`);
    await action();
    info.steps.push({ step, label, elapsed_seconds: (Date.now() - started) / 1000, url: page.url() });
    const deadline = started + captureDuration * 1000 * step / 20;
    while (Date.now() < deadline) await new Promise(done => setTimeout(done, Math.min(15000, deadline - Date.now())));
  }
  await checkpoint("Главная: настоящие опубликованные сеансы", async()=> { await page.goto("/"); await expect(page.locator(".event-card").first()).toBeVisible(); });
  await checkpoint("Каталог и статусы проверенных источников", async()=> { await page.locator("#events").scrollIntoViewIfNeeded(); });
  await checkpoint("Выбор Стерлитамака", async()=> { await page.getByRole("combobox",{name:"Город",exact:true}).selectOption(event.city); await expect(page.locator(".event-card").first()).toBeVisible(); });
  await checkpoint("Спектакли: комбинированные фильтры", async()=> { await page.getByRole("button",{name:"Все фильтры"}).click(); await page.getByRole("combobox",{name:"Категория",exact:true}).selectOption(event.category); });
  await checkpoint("Дата 22 ноября и ссылка с фильтрами", async()=> { await page.getByLabel("С даты",{exact:true}).fill("2026-11-22");await page.getByLabel("По дату",{exact:true}).fill("2026-11-22");await page.locator("#events").scrollIntoViewIfNeeded();await expect(page.locator(".event-card").first()).toBeVisible(); });
  await checkpoint("Реальные диапазоны опубликованных цен", async()=> { await page.locator(".event-card").first().scrollIntoViewIfNeeded(); });
  await checkpoint("Постоянная карточка Мухи-Цокотухи", async()=> { await page.goto(`/events/${event.id}`);await expect(page.locator(".event-detail h1")).toBeVisible();await page.locator(".detail-data").scrollIntoViewIfNeeded(); });
  await checkpoint("Основание цены: опубликованный тариф и первоисточник", async()=> { await expect(page.locator(".detail-description")).toContainText("Цену и наличие билетов конкретного сеанса уточните");await page.locator(".source-block").scrollIntoViewIfNeeded();await expect(page.locator(".source-block")).toContainText("Официальный тариф спектакля"); });
  await checkpoint("Настоящий семантический запрос к настроенной языковой модели", async()=> {
    await page.goto("/agent");await page.getByLabel("Поиск без языковой модели").uncheck();
    const query="Хочу в театр в Стерлитамаке 22 ноября";
    await page.getByLabel("Ваш запрос",{exact:true}).fill(query);
    const responsePromise=page.waitForResponse(r=>r.url().endsWith("/agent/query"),{timeout:90000});await page.getByRole("button",{name:"Найти",exact:true}).click();const response=await responsePromise;const reply=await response.json();
    if(!response.ok() || reply.mode!=="llm") throw new Error(`Actual semantic query did not use a model: ${reply.mode}`);
    info.semantic_query={message:query,mode:reply.mode,trace_id:reply.trace_id,event_ids:reply.events.map(e=>e.id),text:reply.text};
    await expect(page.locator(".chat-message.assistant").last()).toBeVisible();await page.locator(".chat-message.assistant").last().scrollIntoViewIfNeeded();
  });
  await checkpoint("Ответ модели и объяснения из каталога", async()=> { const cards=page.locator(".chat-message.assistant .event-card");if(await cards.count())await cards.first().scrollIntoViewIfNeeded(); });
  await checkpoint("Семейный план: двое взрослых и дети 6 и 10 лет", async()=> {
    await page.getByText("Собрать выходной день",{exact:true}).click();const planner=page.locator("details.planner");
    for(const [name,value] of Object.entries({city:planInput.city,date:planInput.date,budget:"6400",adults:"2",children:"6, 10",start:"10:00",end:"21:00",travel:"20"}))await planner.locator(`input[name=${name}]`).fill(value);
    await planner.getByLabel("Количество событий",{exact:true}).selectOption("2");await planner.scrollIntoViewIfNeeded();
  });
  await checkpoint("План с подтверждённым диапазоном 3400–6400 ₽ на всю семью", async()=> {
    const planner=page.locator("details.planner");const responsePromise=page.waitForResponse(r=>r.url().endsWith("/agent/plan"));await planner.getByRole("button",{name:"Составить план",exact:true}).click();const result=await(await responsePromise).json();
    if(!result.feasible || result.events.length!==2 || result.total_min!==3400 || result.total_max!==6400 || result.travel_status!=="same_venue")throw new Error("Family plan changed during recording");
    info.family_plan={feasible:result.feasible,total_min:result.total_min,total_max:result.total_max,travel_status:result.travel_status,event_ids:result.events.map(e=>e.id),titles:result.events.map(e=>e.title),price_basis_note:result.text};
    await expect(planner.getByText("План найден",{exact:true})).toBeVisible();await planner.locator(".planner-result .notice").scrollIntoViewIfNeeded();
  });
  await checkpoint("Два события на одной площадке: без выдуманного переезда", async()=> { await page.locator(".planner-result .events-grid").scrollIntoViewIfNeeded();await expect(page.locator(".planner-result .event-card")).toHaveCount(2); });
  await checkpoint("Явный поиск без модели: бесплатные спектакли", async()=> {
    await page.getByRole("button",{name:"Начать заново",exact:true}).click();await page.getByLabel("Поиск без языковой модели").check();await page.getByLabel("Ваш запрос",{exact:true}).fill("Покажи бесплатные спектакли в Уфе");
    const responsePromise=page.waitForResponse(r=>r.url().endsWith("/agent/query"));await page.getByRole("button",{name:"Найти",exact:true}).click();const reply=await(await responsePromise).json();if(reply.mode!=="fallback")throw new Error("Explicit model-off fallback was not used");info.fallback={mode:reply.mode,text:reply.text,event_ids:reply.events.map(e=>e.id)};await page.locator(".chat-message.assistant").last().scrollIntoViewIfNeeded();
  });
  await checkpoint("Недоступный бюджет: честный отказ и минимальное изменение", async()=> {
    const planner=page.locator("details.planner");if(await planner.getAttribute("open")===null)await page.getByText("Собрать выходной день",{exact:true}).click();await planner.locator("input[name=budget]").fill("1");
    const responsePromise=page.waitForResponse(r=>r.url().endsWith("/agent/plan"));await planner.getByRole("button",{name:"Составить план",exact:true}).click();const result=await(await responsePromise).json();if(result.feasible)throw new Error("One-ruble family plan unexpectedly succeeded");info.unavailable_plan={feasible:result.feasible,text:result.text,minimal_change:result.minimal_change};await planner.locator(".planner-result .notice").scrollIntoViewIfNeeded();
  });
  await checkpoint("Конструктор iframe: город, категория, период, высота", async()=> { await page.goto("/embed");await page.getByLabel("Город",{exact:true}).fill(event.city);await page.getByLabel("Категория",{exact:true}).fill("спектакль");await page.getByRole("combobox",{name:"Период",exact:true}).selectOption("custom");await page.getByLabel("С даты",{exact:true}).fill("2026-11-22");await page.getByLabel("По дату",{exact:true}).fill("2026-11-22");await page.getByLabel("Высота, px").fill("650"); });
  await checkpoint("Действующий виджет с теми же реальными данными", async()=> { await page.locator("iframe").scrollIntoViewIfNeeded();await expect(page.frameLocator("iframe").getByRole("heading",{level:1})).toBeVisible();await expect(page.frameLocator("iframe").locator(".event-card").first()).toBeVisible(); });
  await checkpoint("Локальная документация API с заблокированными внешними запросами", async()=> {
    info.docs_external_requests=[];await page.route("**/*",route=>{if(new URL(route.request().url()).origin!==base.origin){info.docs_external_requests.push(route.request().url());return route.abort();}return route.continue();});await page.goto("/api/docs");await expect(page.getByRole("heading",{name:/Афиша культуры Башкортостана/})).toBeVisible();
  });
  await checkpoint("Настоящий GET событий из локального Swagger UI", async()=> {const operation=page.locator("#operations-default-events_api_v1_events_get");await operation.locator(".opblock-summary-control").click();await operation.getByRole("button",{name:"Try it out",exact:true}).click();await operation.getByRole("button",{name:"Execute",exact:true}).click();await expect(operation.locator(".responses-wrapper")).toContainText("200");info.docs_offline_assets_verified=info.docs_external_requests.length===0;await operation.locator(".responses-wrapper").scrollIntoViewIfNeeded();});
  await checkpoint("Возвращение к настоящей афише", async()=> {await page.unroute("**/*");await page.goto("/");await expect(page.locator(".event-card").first()).toBeVisible();await page.locator("#events").scrollIntoViewIfNeeded();});
  info.status="recorded";
} catch(error) { failure=error; info.status="failed"; info.error=error instanceof Error ? error.message : String(error); }
finally {
  if(started) info.recording_wall_seconds=(Date.now()-started)/1000;
  await context.close();
  if(video) { await video.saveAs(output); await video.delete(); }
}
if(video) {
  try {
    const verification = await browser.newPage({viewport:{width:1280,height:800}});
    await verification.goto(pathToFileURL(output).href);
    await verification.waitForFunction(()=>document.querySelector("video")?.readyState >= 1,null,{timeout:15000});
    let mediaDuration=await verification.locator("video").evaluate(video=>video.duration);
    if(!Number.isFinite(mediaDuration)) {
      await verification.locator("video").evaluate(video=>{ video.currentTime=1e10; });
      await verification.waitForFunction(()=>Number.isFinite(document.querySelector("video")?.duration),null,{timeout:15000});
      mediaDuration=await verification.locator("video").evaluate(video=>video.duration);
    }
    info.media_duration_seconds=mediaDuration;
    info.media_dimensions=await verification.locator("video").evaluate(video=>({width:video.videoWidth,height:video.videoHeight}));
    info.duration_verified=Number.isFinite(mediaDuration) && mediaDuration >= duration - 1;
    if(info.status === "recorded" && !info.duration_verified) { info.status="failed"; failure=new Error(`Media duration ${mediaDuration} is shorter than ${duration} seconds`); }
    if(info.status==="recorded") {
      info.review_frames=[];
      await verification.locator("video").evaluate(video=>{video.controls=false;video.style.cssText="width:1280px;height:800px;position:absolute;top:0;left:0";document.body.style.margin="0";});
      for(const [label,step] of [["source",8],["llm",9],["plan",12],["fallback",14],["unavailable-plan",15],["widget",17],["local-docs",19]]) {
        const seconds=Math.min(mediaDuration-1,info.steps.find(s=>s.step===step).elapsed_seconds+2);
        await verification.locator("video").evaluate((video,seconds)=>new Promise(done=>{video.addEventListener("seeked",done,{once:true});video.currentTime=seconds;}),seconds);
        const frame=output.replace(/\.webm$/i,"")+`.frame-${label}.png`;
        await verification.locator("video").screenshot({path:frame});info.review_frames.push({label,seconds,path:frame});
      }
    }
    await verification.close();
  } catch(error) { info.duration_verified=false; info.duration_verification_error=String(error); if(!failure) failure=error; info.status="failed"; }
}
info.output=output;
info.finished_at=new Date().toISOString();
await writeFile(output.replace(/\.webm$/i, "")+".json",JSON.stringify(info,null,2)+"\n");
await browser.close();
console.log(JSON.stringify({output,status:info.status,media_duration_seconds:info.media_duration_seconds,duration_verified:info.duration_verified}));
if(failure) throw failure;
