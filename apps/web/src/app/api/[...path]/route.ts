import { NextRequest } from "next/server";
import { createHmac, randomUUID } from "node:crypto";
import { isAllowedOrigin } from "../../../lib/origin";
import { docsHTML } from "../../../lib/docs-html";
export const dynamic = "force-dynamic";
const MAX_BODY = 2_000_000;
async function proxy(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const incomingId = request.headers.get("x-request-id") || "";
  const requestId = /^[A-Za-z0-9_-]{8,80}$/.test(incomingId) ? incomingId : randomUUID();
  const rawCookie = request.cookies.get("ayda_visitor")?.value || "";
  const visitor = /^[a-f0-9-]{36}$/.test(rawCookie) ? rawCookie : randomUUID();
  const secure = (process.env.WEB_PUBLIC_URL || "").startsWith("https:");
  const cookie = `ayda_visitor=${visitor}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000${secure ? "; Secure" : ""}`;
  function finish(response: Response) { response.headers.set("x-request-id", requestId); if (visitor !== rawCookie) { response.headers.append("set-cookie", cookie); response.headers.set("Cache-Control", "private, no-store"); } return response; }
  function failure(status: number, code: string, message: string) { return finish(Response.json({ code, message, details: [], request_id: requestId }, { status })); }
  const { path } = await params;
  const upstream = new URL(process.env.API_INTERNAL_URL || "http://127.0.0.1:8000");
  upstream.pathname = path[0] === "docs" ? "/docs" : path[0] === "openapi.json" ? "/openapi.json" : `/api/${path.map(encodeURIComponent).join("/")}`;
  upstream.search = request.nextUrl.search;
  const headers = new Headers({ "x-request-id": requestId });
  for (const name of ["content-type", "authorization", "idempotency-key"]) { const value = request.headers.get(name); if (value) headers.set(name, value); }
  const secret = process.env.TRUSTED_PROXY_SECRET;
  if (secret) {
    const identity = `web:${visitor}`;
    const timestamp = Math.floor(Date.now() / 1000).toString();
    headers.set("X-Client-Identity", identity);
    headers.set("X-Client-Timestamp", timestamp);
    headers.set("X-Client-Signature", createHmac("sha256", secret).update(`${timestamp}\n${identity}`).digest("hex"));
  }
  try {
    let body: Uint8Array | undefined;
    if (!["GET", "HEAD"].includes(request.method)) {
      const origin = request.headers.get("origin");
      if (origin && !isAllowedOrigin(origin, request.nextUrl.origin, process.env.WEB_PUBLIC_URL)) return failure(403, "forbidden_origin", "Запрос с этого сайта запрещён");
      if (Number(request.headers.get("content-length")) > MAX_BODY) return failure(413, "body_too_large", "Запрос слишком большой");
      const reader = request.body?.getReader(); const chunks: Uint8Array[] = []; let total = 0;
      if (reader) while (true) { const part = await reader.read(); if (part.done) break; total += part.value.byteLength; if (total > MAX_BODY) { await reader.cancel(); return failure(413, "body_too_large", "Запрос слишком большой"); } chunks.push(part.value); }
      body = new Uint8Array(total); let position = 0; for (const chunk of chunks) { body.set(chunk, position); position += chunk.byteLength; }
    }
    const response = await fetch(upstream, { method: request.method, headers, body: body as BodyInit | undefined, cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(90000) });
    const resultHeaders = new Headers();
    for (const name of ["content-type", "cache-control", "etag", "retry-after"]) { const value = response.headers.get(name); if (value) resultHeaders.set(name, value); }
    let result: ArrayBuffer | string | null = [204,304].includes(response.status) || request.method === "HEAD" ? null : await response.arrayBuffer();
    if (path[0] === "docs" && result) result = docsHTML(new TextDecoder().decode(result as ArrayBuffer));
    return finish(new Response(result, { status: response.status, headers: resultHeaders }));
  } catch { return failure(503, "unavailable", "Сервис афиши временно недоступен. Попробуйте позже."); }
}
export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
