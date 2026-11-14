import { expect, test } from "@playwright/test";

test("web identities are HttpOnly, stable across refresh, and distinct between visitors",async ({browser})=>{
  const first=await browser.newContext(), second=await browser.newContext();
  try {
    const a=await first.newPage(), b=await second.newPage();
    const base=process.env.WEB_TEST_URL || "http://127.0.0.1:3000";
    await a.goto(base); await b.goto(base);
    const visitor=(await first.cookies()).find(c=>c.name==="ayda_visitor")!;
    expect(visitor.httpOnly).toBe(true); expect(visitor.sameSite).toBe("Lax");
    expect((await second.cookies()).find(c=>c.name==="ayda_visitor")?.value).not.toBe(visitor.value);
    expect(await a.evaluate(()=>document.cookie)).not.toContain("ayda_visitor");
    await a.reload();
    expect((await first.cookies()).find(c=>c.name==="ayda_visitor")?.value).toBe(visitor.value);
  } finally { await first.close(); await second.close(); }
});

test("web proxy strips spoofed identity and uses complete Russian error format",async ({request})=>{
  const spoof=await request.get("/api/v1/cities",{headers:{"X-Client-Identity":"attacker:forged", "X-Client-Timestamp":"0", "X-Client-Signature":"forged"}});
  expect(spoof.status()).toBe(200);
  const large=await request.post("/api/v1/feedback",{data:"x".repeat(2_000_001),headers:{"Content-Type":"application/json"}});
  expect(large.status()).toBe(413);
  const error=await large.json();
  expect(error).toMatchObject({code:"body_too_large",details:[],message:"Запрос слишком большой"});
  expect(error.request_id).toBe(large.headers()["x-request-id"]);
  const cross=await request.post("/api/v1/feedback",{headers:{Origin:"https://untrusted.example"},data:{message:"must not be accepted"}});
  expect(cross.status()).toBe(403);
});

test("technical SEO uses public origin and sitemap contains only published events",async ({request})=>{
  const robots=await request.get("/robots.txt"); expect(await robots.text()).toContain("Disallow: /admin");
  const listing=await request.get("/api/v1/events?page_size=1"); const event=(await listing.json()).items[0];
  const detail=await request.get(`/events/${event.id}`); const html=await detail.text();
  expect(html).toContain('rel="canonical"'); expect(html).toContain(`/events/${event.id}`);
  const sitemap=await request.get("/sitemap.xml"); expect(sitemap.status()).toBe(200); expect(await sitemap.text()).toContain(`/events/${event.id}`);
  expect(await sitemap.text()).not.toContain("audit-nonexistent-id");
});


test("same-origin browser POST works with standalone loopback hostname aliases",async ({page})=>{
  await page.goto("/agent");
  const response=await page.evaluate(async()=>{
    const result=await fetch('/api/v1/agent/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:'Покажи спектакли в Уфе',offline:true})});
    return {status:result.status,body:await result.json()};
  });
  expect(response.status).toBe(200);
  expect(response.body.mode).toBe('fallback');
  expect(response.body.text).toBeTruthy();
});
