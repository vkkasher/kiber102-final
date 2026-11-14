import type { EventOutput, EventListOutput } from "./generated-api";
export type Event = EventOutput;
export type Listing = EventListOutput;
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try { res = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  }); } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new Error("Нет связи с сервисом афиши. Проверьте соединение и повторите запрос");
  }
  if (!res.ok) {
    let message = `Сервис ответил с ошибкой ${res.status}`;
    try {
      const e = await res.json();
      message =
        e.message ||
        e.detail?.message ||
        (typeof e.detail === "string" ? e.detail : message);
      if (Array.isArray(e.details) && e.details.length)
        message +=
          ": " +
          e.details
            .slice(0, 4)
            .map((d: { field?: string; message?: string }) =>
              [d.field, d.message].filter(Boolean).join(" "),
            )
            .join("; ");
    } catch { message = `Сервис ответил с ошибкой ${res.status}. Повторите запрос позже`; }
    throw new Error(message);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}
export function price(e: Event) {
  if (e.is_free) return "Бесплатно";
  if (e.price_min == null) return "Цена уточняется";
  if (e.price_max == null) return `От ${e.price_min.toLocaleString("ru-RU")} ₽`;
  if (e.price_max != null && e.price_max !== e.price_min)
    return `${e.price_min.toLocaleString("ru-RU")}–${e.price_max.toLocaleString("ru-RU")} ₽`;
  return `${e.price_min.toLocaleString("ru-RU")} ₽`;
}
export function date(value: string, full = false) {
  const d = new Date(value);
  return Number.isNaN(+d)
    ? "Дата уточняется"
    : new Intl.DateTimeFormat("ru-RU", {
        timeZone: "Asia/Yekaterinburg",
        day: "numeric",
        month: full ? "long" : "short",
        ...(full ? { year: "numeric" as const } : {}),
        hour: "2-digit",
        minute: "2-digit",
      }).format(d);
}
export function safeURL(value?: string | null) {
  if (!value) return undefined;
  try {
    const u = new URL(value);
    return ["http:", "https:"].includes(u.protocol) ? u.href : undefined;
  } catch {
    return undefined;
  }
}
export const statusName: Record<string, string> = {
  published: "Опубликовано",
  cancelled: "Отменено",
  rescheduled: "Перенесено",
  draft: "Черновик",
};
