"use client";
import { useState, useEffect } from "react";
import { periodRange } from "../../lib/calendar";
import { Shell } from "../../components/ui";
export default function Embed() {
  const [city, setCity] = useState(""),
    [category, setCategory] = useState(""),
    [period, setPeriod] = useState("all"),
    [dateFrom, setDateFrom] = useState(""),
    [dateTo, setDateTo] = useState(""),
    [height, setHeight] = useState("650"),
    [origin, setOrigin] = useState("");
  useEffect(() => setOrigin(location.origin), []);
  const p = new URLSearchParams();
  if (city) p.set("city", city);
  if (category) p.set("category", category);
  if (period !== "all") {
    const range =
      period === "custom"
        ? { date_from: dateFrom, date_to: dateTo }
        : periodRange(period);
    if (range.date_from) p.set("date_from", range.date_from);
    if (range.date_to) p.set("date_to", range.date_to);
  }
  const frameHeight = Math.min(2000, Math.max(350, Number(height) || 650));
  const src = `${origin}/widget${p.size ? "?" + p : ""}`;
  const code = `<iframe\n  src="${src.replace(/"/g, "&quot;")}"\n  title="Афиша культуры Башкортостана"\n  width="100%" height="${frameHeight}"\n  style="border:0" loading="lazy"\n  referrerpolicy="strict-origin-when-cross-origin"\n></iframe>`;
  const [copyStatus, setCopyStatus] = useState("");
  return (
    <Shell>
      <div className="inner-page embed-page">
        <div className="page-intro">
          <h1>Культура на вашем сайте.</h1>
          <p>
            Независимый виджет показывает ту же проверенную афишу. Настройте
            город и категорию, затем вставьте код на свою страницу.
          </p>
        </div>
        <div className="embed-controls">
          <label>
            Город
            <input
              value={city}
              onChange={(e) => setCity(e.target.value)}
              placeholder="Весь Башкортостан"
            />
          </label>
          <label>
            Категория
            <input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="Все направления"
            />
          </label>
          <label>
            Период
            <select
              aria-label="Период"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
            >
              <option value="all">Все даты</option>
              <option value="today">Сегодня</option>
              <option value="tomorrow">Завтра</option>
              <option value="weekend">Выходные</option>
              <option value="week">Ближайшие 7 дней</option>
              <option value="custom">Указать даты</option>
            </select>
          </label>
          {period === "custom" && (
            <>
              <label>
                С даты
                <input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                />
              </label>
              <label>
                По дату
                <input
                  type="date"
                  min={dateFrom}
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                />
              </label>
            </>
          )}
          <label>
            Высота, px
            <input
              type="number"
              min="350"
              max="2000"
              value={height}
              onChange={(e) => setHeight(e.target.value)}
            />
          </label>
        </div>
        <section className="embed-code" aria-labelledby="embed-code-title">
          <div className="embed-section-heading">
            <h2 id="embed-code-title">Код для вставки</h2>
            <button
              className="button"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(code);
                  setCopyStatus("Код скопирован");
                } catch {
                  setCopyStatus("Выделите код ниже и скопируйте его вручную");
                }
              }}
            >
              Скопировать код
            </button>
          </div>
          <p className="embed-copy-status" role="status">
            {copyStatus}
          </p>
          <pre
            className="embed-code-block"
            role="region"
            aria-label="HTML-код виджета"
          >
            <code>{code}</code>
          </pre>
          <p className="embed-note">
            Вставьте код в HTML вашей страницы. Виджет обновляется вместе с
            афишей, не меняет стили сайта и работает без сторонних cookies.
          </p>
        </section>
        <div className="embed-section-heading embed-preview-heading">
          <h2>Предпросмотр</h2>
          <p>Так афиша будет выглядеть у вас на сайте</p>
        </div>
        <iframe
          className="embed-frame"
          src={src || "/widget"}
          title="Предпросмотр афиши"
          style={{ height: frameHeight }}
        />
      </div>
    </Shell>
  );
}
