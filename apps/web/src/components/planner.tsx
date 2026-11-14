"use client";
import { FormEvent, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { PlanOutput, PlanRequest } from "../lib/generated-api";
import { EventCard } from "./ui";

export function Planner({ standalone = false }: { standalone?: boolean }) {
  const Container = standalone ? "section" : "details";
  const [busy, setBusy] = useState(false),
    [result, setResult] = useState<PlanOutput | null>(null),
    [error, setError] = useState(""),
    [categories, setCategories] = useState<string[]>([]);
  useEffect(() => {
    api<string[]>("/categories")
      .then(setCategories)
      .catch(() => setCategories([]));
  }, []);
  async function plan(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    setError("");
    setBusy(true);
    setResult(null);
    try {
      const children = String(fd.get("children") || "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
        .map(Number);
      if (children.some((n) => !Number.isInteger(n) || n < 0 || n > 17))
        throw new Error("Укажите возраст детей от 0 до 17 лет через запятую");
      const input: PlanRequest = {
        city: String(fd.get("city") || ""),
        date: String(fd.get("date") || ""),
        window_start: String(fd.get("start") || ""),
        window_end: String(fd.get("end") || ""),
        budget: Number(fd.get("budget")),
        adults: Number(fd.get("adults")),
        children_ages: children,
        max_travel_minutes: Number(fd.get("travel")),
        interests: fd.get("interest") ? [String(fd.get("interest"))] : [],
        event_count: Number(fd.get("event_count")) || null,
      };
      const r = await api<PlanOutput>("/agent/plan", {
        method: "POST",
        body: JSON.stringify(input),
      });
      setResult(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Container className={`planner${standalone ? " planner-standalone" : ""}`}>
      {standalone ? (
        <h2>Собрать выходной день</h2>
      ) : (
        <summary>Собрать выходной день</summary>
      )}
      <p>
        Несколько событий в один день с общим бюджетом. Время переездов
        учитывается только при наличии надёжных данных о маршруте.
      </p>
      <form onSubmit={plan}>
        <label>
          Город
          <input name="city" placeholder="Уфа" required maxLength={100} />
        </label>
        <label>
          Дата
          <input name="date" type="date" required />
        </label>
        <label>
          Направление
          <select aria-label="Направление" name="interest" defaultValue="">
            <option value="">Любое направление</option>
            {categories.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </label>
        <label>
          Количество событий
          <select
            aria-label="Количество событий"
            name="event_count"
            defaultValue=""
          >
            <option value="">От двух до четырёх</option>
            {[2, 3, 4].map((count) => (
              <option key={count} value={count}>
                {count}
              </option>
            ))}
          </select>
        </label>
        <label>
          Общий бюджет, ₽
          <input
            name="budget"
            type="number"
            min="0"
            max="1000000"
            required
            placeholder="1500"
          />
        </label>
        <label>
          Начало дня
          <input name="start" type="time" required defaultValue="10:00" />
        </label>
        <label>
          Конец дня
          <input name="end" type="time" required defaultValue="20:00" />
        </label>
        <label>
          Взрослых
          <input
            name="adults"
            type="number"
            min="1"
            max="20"
            defaultValue="1"
            required
          />
        </label>
        <label>
          Возраст детей через запятую
          <input name="children" placeholder="5, 10" maxLength={100} />
        </label>
        <label>
          Переезд до, мин.
          <input
            name="travel"
            type="number"
            min="0"
            max="180"
            defaultValue="30"
            required
          />
        </label>
        <button className="button" disabled={busy}>
          {busy ? "Составляем…" : "Составить план"}
        </button>
      </form>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {result && (
        <div className="planner-result" aria-live="polite">
          <div className="notice">
            <strong>
              {result.feasible
                ? "План найден"
                : "Пока не удалось составить план"}
            </strong>
            <p>{result.text}</p>
            {result.total_min != null && (
              <p>
                Стоимость для всей компании:{" "}
                {result.total_min.toLocaleString("ru-RU")}{" "}
                {result.total_max != null &&
                result.total_max !== result.total_min
                  ? `– ${result.total_max.toLocaleString("ru-RU")}`
                  : ""}{" "}
                ₽
              </p>
            )}
            <small>
              Переезды:{" "}
              {(
                {
                  unknown: "Время не подтверждено",
                  not_applicable: "Не рассчитывались",
                  same_venue: "События на одной площадке",
                  estimated: "Время оценено по маршруту",
                } as Record<string, string>
              )[result.travel_status] || "Статус не подтверждён"}
            </small>
            {result.travel_mode && (
              <p>
                <small>
                  Способ передвижения:{" "}
                  {(
                    {
                      driving: "Автомобиль",
                      walking: "Пешком",
                      cycling: "Велосипед",
                      transit: "Общественный транспорт",
                      mixed: "Разные способы",
                      none: "В пределах одной площадки",
                      unknown: "Не подтверждён",
                    } as Record<string, string>
                  )[result.travel_mode] || "Не подтверждён"}
                  .
                </small>
              </p>
            )}
            {result.travel_mode === "driving" && (
              <p>
                <small>
                  Время поездки оценено для автомобиля без учёта пробок и
                  парковки. Для другого способа передвижения время пути не
                  подтверждено.
                </small>
              </p>
            )}
            {result.budget_scope === "event_tickets_only" && (
              <p>
                <small>
                  Общая стоимость включает только билеты. Расходы на дорогу не
                  рассчитаны.
                </small>
              </p>
            )}
          </div>
          <div className="events-grid">
            {result.events.map((e) => (
              <EventCard key={e.id} event={e} />
            ))}
          </div>
        </div>
      )}
    </Container>
  );
}
