import type { MetadataRoute } from "next";
import type { Listing } from "../lib/api";
import { publicURL } from "../lib/server-api";
export const dynamic = "force-dynamic";
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const result: MetadataRoute.Sitemap = [{ url: publicURL().href }];
  let page = 1;
  while (true) {
    const response = await fetch(`${process.env.API_INTERNAL_URL || "http://127.0.0.1:8000"}/api/v1/events?page=${page}&page_size=100`, { cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (!response.ok) throw new Error("Не удалось обновить карту сайта");
    const data: Listing = await response.json();
    for (const event of data.items) if (event.status === "published") result.push({ url: new URL(`/events/${encodeURIComponent(event.id)}`, publicURL()).href });
    if (page * data.page_size >= data.total || !data.items.length) break;
    page++;
  }
  return result;
}
