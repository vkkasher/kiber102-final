import "server-only";
import { cache } from "react";
import type { Event } from "./api";
export function publicURL() {
  return new URL(process.env.WEB_PUBLIC_URL || "http://localhost:3000");
}
export const publicEvent = cache(async (id: string): Promise<Event | null> => {
  const response = await fetch(`${process.env.API_INTERNAL_URL || "http://127.0.0.1:8000"}/api/v1/events/${encodeURIComponent(id)}`, { cache: "no-store", signal: AbortSignal.timeout(8000) });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("Сервис афиши временно недоступен. Повторите загрузку позже.");
  const event: Event = await response.json();
  return ["published", "cancelled", "rescheduled"].includes(event.status) ? event : null;
});
