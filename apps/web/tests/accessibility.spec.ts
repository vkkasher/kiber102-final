import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function scan(page: Page) {
  const result = await new AxeBuilder({page}).withTags(["wcag2a","wcag2aa","wcag21aa","best-practice"]).analyze();
  // WCAG 1.4.3 excludes text that is purely decorative. This hidden brand
  // ornament carries no information; all other contrast findings remain failures.
  return result.violations.map(v=>({...v,nodes:v.nodes.filter(n=>!(v.id==="color-contrast" && n.html.includes('class="editorial-symbol"') && n.html.includes('aria-hidden="true"')))})).filter(v=>v.nodes.length);
}
for(const width of [360,390,768,1280]) test(`public accessibility, contrast and layout at ${width}px`, async({page,request},info)=>{
  test.setTimeout(90000);
  const listing=await (await request.get("/api/v1/events?page_size=1")).json();
  const paths=["/","/events","/planner","/agent","/feedback","/embed","/widget","/credits",`/events/${listing.items[0].id}`,"/events/ux-audit-nonexistent","/admin","/api/docs"];
  await page.setViewportSize({width,height:900});
  for(const path of paths){
    await page.goto(path);
    if(path==="/"||path==="/widget") await expect(page.locator(".result-count")).toBeVisible();
    if(path==="/api/docs") await expect(page.getByRole("heading",{name:/Афиша культуры/})).toBeVisible();
    const violations=await scan(page);
    await info.attach(`axe-${width}-${encodeURIComponent(path)}`,{body:JSON.stringify(violations,null,2),contentType:"application/json"});
    expect(violations,`${width}px ${path}`).toEqual([]);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${width}px ${path} overflow`).toBe(true);
  }
});

test("mobile navigation opens into its links and Escape restores the trigger",async({page})=>{
  await page.setViewportSize({width:360,height:900});await page.goto("/");
  const trigger=page.getByRole("button",{name:"Открыть меню",exact:true});
  await trigger.focus();await page.keyboard.press("Enter");
  await expect(page.getByRole("link",{name:"Афиша",exact:true})).toBeFocused();
  await page.keyboard.press("Tab");await expect(page.getByRole("link",{name:"ИИ-помощник",exact:true})).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button",{name:"Открыть меню",exact:true})).toBeFocused();
  await expect(trigger).toHaveAttribute("aria-expanded","false");
});

test("reduced motion disables scripted smooth scrolling and keyboard focus stays visible",async({page})=>{
  await page.emulateMedia({reducedMotion:"reduce"});
  await page.addInitScript(()=>{
    const original=Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView=function(options){
      document.documentElement.dataset.scrollBehavior=typeof options==="object"?String(options.behavior):"default";
      original.call(this,options);
    };
  });
  await page.goto("/");const category=page.getByRole("button",{name:"Театр",exact:true});await category.focus();
  const outline=await category.evaluate(e=>({width:getComputedStyle(e).outlineWidth,style:getComputedStyle(e).outlineStyle}));
  expect(parseFloat(outline.width)).toBeGreaterThanOrEqual(2);expect(outline.style).not.toBe("none");
  await page.keyboard.press("Enter");await expect(page.locator("html")).toHaveAttribute("data-scroll-behavior","auto");
});

test("widget works on an isolated host without cookies and preserves host styles",async({page,context})=>{
  const origin=new URL(process.env.WEB_TEST_URL||"http://127.0.0.1:3000").origin;
  let readsWithoutCookies=0;
  await context.route("**/api/v1/**",async route=>{const headers={...route.request().headers()};delete headers.cookie;readsWithoutCookies++;await route.continue({headers});});
  await page.setViewportSize({width:390,height:900});
  await page.setContent(`<html lang="ru"><head><style>body{margin:8px;background:rgb(255,238,255)}.event-card{border:30px solid red}iframe{width:100%;height:650px;border:0}</style></head><body><h1>Принимающая страница</h1><iframe title="Афиша" src="${origin}/widget?city=${encodeURIComponent("Уфа")}"></iframe></body></html>`);
  const widget=page.frameLocator("iframe");await expect(widget.getByRole("heading",{level:1,name:"Афиша событий"})).toBeVisible();await expect(widget.locator(".result-count")).toBeVisible();
  expect(readsWithoutCookies).toBeGreaterThan(0);
  expect(await page.locator("body").evaluate(e=>getComputedStyle(e).backgroundColor)).toBe("rgb(255, 238, 255)");
  expect(await page.locator("iframe").evaluate(e=>Math.round(e.getBoundingClientRect().height))).toBe(650);
  expect(await widget.locator(".event-card").first().evaluate(e=>getComputedStyle(e).borderTopWidth)).not.toBe("30px");
});

test("docs retain a usable keyboard-operated read-only request after accessibility fixes",async({page})=>{
  await page.goto("/api/docs");await expect(page.getByRole("heading",{name:/Афиша культуры/})).toBeVisible();
  const operation=page.locator("#operations-default-events_api_v1_events_get");
  await operation.locator(".opblock-summary-control").focus();await page.keyboard.press("Enter");
  await operation.getByRole("button",{name:"Try it out",exact:true}).click();
  await operation.getByRole("button",{name:"Execute",exact:true}).click();
  await expect(operation.locator(".responses-wrapper")).toContainText("200");
  expect(await scan(page)).toEqual([]);
});

test("docs load and execute using only local assets when external requests are blocked",async({page})=>{
  const origin=new URL(process.env.WEB_TEST_URL||"http://127.0.0.1:3000").origin;
  const external:string[]=[];
  await page.route("**/*",route=>{
    if(new URL(route.request().url()).origin!==origin){external.push(route.request().url());return route.abort();}
    return route.continue();
  });
  await page.goto("/api/docs");await expect(page.getByRole("heading",{name:/Афиша культуры/})).toBeVisible();
  const operation=page.locator("#operations-default-events_api_v1_events_get");await operation.locator(".opblock-summary-control").click();await operation.getByRole("button",{name:"Try it out",exact:true}).click();await operation.getByRole("button",{name:"Execute",exact:true}).click();await expect(operation.locator(".responses-wrapper")).toContainText("200");
  expect(external).toEqual([]);
});
