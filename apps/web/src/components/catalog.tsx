"use client";
import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { localDay, periodRange } from "../lib/calendar";
import { FeaturedEvents } from "./featured-events";
import { CatalogAssistant } from "./catalog-assistant";
import { api, Listing } from "../lib/api";
import {
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  ErrorNotice,
  EventCard,
  MapPin,
  Search,
  Shell,
} from "./ui";
type Filters = {
  q: string;
  city: string;
  category: string;
  date_from: string;
  date_to: string;
  price_max: string;
  age: string;
  venue: string;
  sort: string;
  page: string;
};
const base: Filters = {
  q: "",
  city: "",
  category: "",
  date_from: "",
  date_to: "",
  price_max: "",
  age: "",
  venue: "",
  sort: "date",
  page: "1",
};
const kinds = ["Театр", "Музыка", "Выставки", "Детям"];
const categoryValues = ["спектакль", "концерт", "выставка", ""];

export function Catalog({
  widget = false,
  landing = true,
}: {
  widget?: boolean;
  landing?: boolean;
}) {
  const [filters, setFilters] = useState<Filters>(base),
    [ready, setReady] = useState(false),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [data, setData] = useState<Listing | null>(null),
    [cities, setCities] = useState<string[]>([]),
    [categories, setCategories] = useState<string[]>([]),
    [venues, setVenues] = useState<string[]>([]),
    [advanced, setAdvanced] = useState(false),
    [refresh, setRefresh] = useState(0);

  useEffect(() => {
    function read() {
      const p = new URLSearchParams(location.search);
      setFilters({
        ...base,
        ...Object.fromEntries([...p].filter(([k]) => k in base)),
      });
      setReady(true);
    }
    read();
    addEventListener("popstate", read);
    api<string[]>("/cities")
      .then(setCities)
      .catch(() => {});
    api<string[]>("/categories")
      .then(setCategories)
      .catch(() => {});
    api<string[]>("/venues")
      .then(setVenues)
      .catch(() => {});
    return () => removeEventListener("popstate", read);
  }, []);

  useEffect(() => {
    if (!ready) return;
    let current = true;
    setLoading(true);
    setError("");
    const p = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => {
      if (v) p.set(k, v);
    });
    p.set("page_size", widget ? "6" : "12");
    api<Listing>(`/events?${p}`)
      .then((result) => {
        if (current) setData(result);
      })
      .catch((e) => {
        if (current) {
          setError(e.message);
          setData(null);
        }
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [filters, ready, refresh, widget]);

  function change(patch: Partial<Filters>) {
    const next = { ...filters, ...patch, page: patch.page || "1" };
    setFilters(next);
    if (patch.page)
      document.getElementById("events")?.scrollIntoView({
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
      });
    const p = new URLSearchParams();
    Object.entries(next).forEach(([k, v]) => {
      if (v && v !== base[k as keyof Filters]) p.set(k, v);
    });
    history.pushState(null, "", `${location.pathname}${p.size ? "?" + p : ""}`);
  }

  function chooseCategory(patch: Partial<Filters>) {
    change(patch);
    document.getElementById("events")?.scrollIntoView({
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
    });
  }

  const CatalogHeading = widget ? "h1" : "h2";
  const filtered = Object.entries(filters).some(
    ([k, v]) => v !== base[k as keyof Filters],
  );

  const content = (
    <div className={widget ? "widget-content" : "page-wrap"}>
      {!widget && (
        <>
          <nav className="discovery-nav" aria-label="Направления">
            <button
              aria-pressed={!filters.category && !filters.age}
              className={!filters.category && !filters.age ? "active" : ""}
              onClick={() => chooseCategory({ category: "", age: "" })}
            >
              Все события
            </button>
            {kinds.map((kind, index) => (
              <button
                key={kind}
                aria-pressed={
                  index === 3
                    ? !!filters.age
                    : filters.category === categoryValues[index]
                }
                className={
                  (
                    index === 3
                      ? !!filters.age
                      : filters.category === categoryValues[index]
                  )
                    ? "active"
                    : ""
                }
                onClick={() =>
                  chooseCategory(
                    index === 3
                      ? { age: "6", category: "" }
                      : { category: categoryValues[index], age: "" },
                  )
                }
              >
                {kind}
              </button>
            ))}
            <Link className="discovery-help" href="/agent">
              Подобрать по интересам <ArrowUpRight size={16} />
            </Link>
          </nav>
          {landing ? (
            <>
              {filtered && (
                <section className="discovery-intro">
                  <h1>Культура, которая рядом</h1>
                  <p>
                    Театры, музыка и встречи
                    <br />в Башкортостане
                  </p>
                </section>
              )}
              {!filtered && (
                <FeaturedEvents events={data?.items || []} loading={loading} />
              )}
            </>
          ) : (
            <section className="discovery-intro catalog-intro">
              <h1>Афиша</h1>
              <p>
                Выберите свой день
                <br />в культурной жизни республики
              </p>
            </section>
          )}
        </>
      )}
      <section aria-label="Поиск событий" className="search-section">
        <form
          className="search-bar"
          onSubmit={(e) => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            change({ q: String(fd.get("q") || "") });
          }}
        >
          <label className="search-city">
            <MapPin size={20} />
            <span className="sr-only">Город</span>
            <select
              value={filters.city}
              onChange={(e) => change({ city: e.target.value })}
            >
              <option value="">Весь Башкортостан</option>
              {cities.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
          <label className="search-query">
            <Search size={22} />
            <span className="sr-only">Название, место или интерес</span>
            <input
              name="q"
              key={filters.q}
              defaultValue={filters.q}
              placeholder="Название, место или интерес"
            />
          </label>
          <button className="button" type="submit">
            Найти события <ArrowRight size={19} />
          </button>
        </form>
        {!widget && <CatalogAssistant />}
        {!widget && (
          <div className="date-rail" role="group" aria-label="Выбрать день">
            <button
              aria-pressed={!filters.date_from && !filters.date_to}
              className={
                !filters.date_from && !filters.date_to
                  ? "date-cell active"
                  : "date-cell"
              }
              onClick={() => change({ date_from: "", date_to: "" })}
            >
              <CalendarDays size={18} />
              <strong>Все даты</strong>
            </button>
            {ready && Array.from({ length: 10 }, (_, index) => {
              const day = localDay(index);
              const dayDate = new Date(`${day}T12:00:00Z`);
              return (
                <button
                  key={day}
                  className={`date-cell ${filters.date_from === day && filters.date_to === day ? "active" : ""}`}
                  aria-pressed={
                    filters.date_from === day && filters.date_to === day
                  }
                  onClick={() => change({ date_from: day, date_to: day })}
                >
                  <span>
                    {new Intl.DateTimeFormat("ru-RU", {
                      weekday: "short",
                      timeZone: "Asia/Yekaterinburg",
                    }).format(dayDate)}
                  </span>
                  <strong>{dayDate.getUTCDate()}</strong>
                  <small>
                    {new Intl.DateTimeFormat("ru-RU", {
                      month: "short",
                      timeZone: "Asia/Yekaterinburg",
                    }).format(dayDate)}
                  </small>
                </button>
              );
            })}
          </div>
        )}
        <div className="quick-filters">
          {[
            "Сегодня",
            "Завтра",
            "Выходные",
            "На неделе",
            "Детям",
            "Бесплатно",
          ].map((v, i) => (
            <button
              key={v}
              className="chip"
              onClick={() => {
                if (i === 4) change({ age: "6" });
                else if (i === 5) change({ price_max: "0" });
                else
                  change(
                    periodRange(["today", "tomorrow", "weekend", "week"][i]),
                  );
              }}
            >
              {i === 0 && <CalendarDays size={15} />} {v}
            </button>
          ))}
          <button
            className={`chip filter-toggle ${advanced ? "selected" : ""}`}
            aria-expanded={advanced}
            onClick={() => setAdvanced(!advanced)}
          >
            Все фильтры <span>{advanced ? "−" : "+"}</span>
          </button>
        </div>
        {advanced && (
          <div className="advanced-filters">
            <label>
              Категория
              <select
                value={filters.category}
                onChange={(e) => change({ category: e.target.value })}
              >
                <option value="">Все категории</option>
                {categories.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
            <label>
              С даты
              <input
                type="date"
                value={filters.date_from}
                onChange={(e) => change({ date_from: e.target.value })}
              />
            </label>
            <label>
              По дату
              <input
                type="date"
                min={filters.date_from}
                value={filters.date_to}
                onChange={(e) => change({ date_to: e.target.value })}
              />
            </label>
            <label>
              Цена до, ₽
              <input
                type="number"
                min="0"
                value={filters.price_max}
                placeholder="Любая"
                onChange={(e) => change({ price_max: e.target.value })}
              />
            </label>
            <label>
              Возраст ребёнка
              <input
                type="number"
                min="0"
                max="99"
                value={filters.age}
                placeholder="Без ограничения"
                onChange={(e) => change({ age: e.target.value })}
              />
            </label>
            <label>
              Площадка
              <select
                value={filters.venue}
                onChange={(e) => change({ venue: e.target.value })}
              >
                <option value="">Все площадки</option>
                {venues.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
          </div>
        )}
      </section>
      <section id="events" className="catalog-section">
        <div className="section-heading">
          <div>
            <CatalogHeading>
              {filters.category || "Афиша событий"}
            </CatalogHeading>
            {data && (
              <p className="result-count">
                {data.total} событий по вашему запросу
              </p>
            )}
          </div>
          <label className="sort-label">
            <span className="sr-only">Сортировка</span>
            <select
              value={filters.sort}
              onChange={(e) => change({ sort: e.target.value })}
            >
              <option value="date">Сначала ближайшие</option>
              <option value="price">Сначала дешевле</option>
            </select>
          </label>
        </div>
        {filtered && (
          <div className="active-filters">
            {Object.entries(filters)
              .filter(
                ([k, v]) => v !== base[k as keyof Filters] && k !== "page",
              )
              .map(([k, v]) => (
                <button
                  className="chip selected"
                  key={k}
                  onClick={() => change({ [k]: base[k as keyof Filters] })}
                >
                  {
                    (
                      {
                        city: "Город",
                        q: "Поиск",
                        category: "Категория",
                        date_from: "С",
                        date_to: "По",
                        price_max: "До ₽",
                        age: "Возраст",
                        venue: "Площадка",
                        sort: "Сортировка",
                      } as Record<string, string>
                    )[k]
                  }
                  : {v} ×
                </button>
              ))}
            <button className="text-button" onClick={() => change(base)}>
              Сбросить всё
            </button>
          </div>
        )}
        {loading ? (
          <div
            className="events-grid"
            role="status"
            aria-live="polite"
            aria-label="Загружаем события"
          >
            {[1, 2, 3].map((n) => (
              <div className="skeleton" key={n}>
                <div />
                <span />
                <span />
              </div>
            ))}
          </div>
        ) : error ? (
          <ErrorNotice message={error} retry={() => setRefresh((v) => v + 1)} />
        ) : data?.items.length ? (
          <>
            <div className="events-grid">
              {data.items.map((e) => (
                <EventCard event={e} key={e.id} headingLevel={widget ? 2 : 3} />
              ))}
            </div>
            {data.total > data.page_size && (
              <nav className="pagination" aria-label="Страницы каталога">
                <button
                  className="button secondary"
                  disabled={data.page <= 1}
                  onClick={() => change({ page: String(data.page - 1) })}
                >
                  Назад
                </button>
                <span>
                  Страница {data.page} из{" "}
                  {Math.ceil(data.total / data.page_size)}
                </span>
                <button
                  className="button secondary"
                  disabled={data.page * data.page_size >= data.total}
                  onClick={() => change({ page: String(data.page + 1) })}
                >
                  Далее
                </button>
              </nav>
            )}
          </>
        ) : (
          <div className="empty-state">
            <div className="empty-symbol">
              <Search size={32} />
            </div>
            <div>
              <h3>
                {filtered
                  ? "Пока ничего не нашлось"
                  : "Афиша готовится к открытию"}
              </h3>
              <p>
                {filtered
                  ? "Попробуйте другой день, город или более широкий бюджет."
                  : "Здесь появятся события после проверки источников. Мы не заполняем афишу выдуманными мероприятиями."}
              </p>
              <div className="empty-actions">
                {filtered ? (
                  <button
                    className="button secondary"
                    onClick={() => change(base)}
                  >
                    Сбросить фильтры
                  </button>
                ) : (
                  <Link className="button secondary" href="/feedback">
                    Предложить событие <ArrowUpRight size={17} />
                  </Link>
                )}
                <Link className="text-button" href="/agent">
                  Спросить помощника <ArrowRight size={17} />
                </Link>
              </div>
            </div>
          </div>
        )}
      </section>
      {!widget && (
        <section className="editorial-note">
          <div className="editorial-manifesto">
            <h2>Ваш следующий выходной</h2>
            <p>
              Театр с детьми, вечер вдвоём или целый день в городе. Расскажите о
              компании и бюджете, а мы соберём подходящие события из афиши.
            </p>
            <Link className="editorial-cta" href="/planner">
              Найти свой план <ArrowUpRight size={18} />
            </Link>
            <div className="editorial-mosaic">
              <figure>
                <img
                  src="/venues/gafuri-drama.jpg"
                  alt="Вечерний фасад Башкирского академического театра драмы имени М. Гафури в Уфе"
                  width="560"
                  height="320"
                  loading="lazy"
                />
                <figcaption>Театр драмы им. М. Гафури · Уфа</figcaption>
              </figure>
              <figure>
                <img
                  src="/venues/gkz-bashkortostan.jpg"
                  alt="Фасад Государственного концертного зала Башкортостан в Уфе"
                  width="560"
                  height="320"
                  loading="lazy"
                />
                <figcaption>ГКЗ «Башкортостан» · Уфа</figcaption>
              </figure>
            </div>
          </div>
          <Subscription city={filters.city} category={filters.category} />
        </section>
      )}
      {widget && (
        <a
          className="widget-credit"
          href="/"
          target="_blank"
          rel="noopener noreferrer"
        >
          Афиша Башкортостана / Культура <ArrowUpRight size={14} />
        </a>
      )}
    </div>
  );
  return widget ? (
    <main id="content" className="widget" aria-label="Афиша в виджете">
      {content}
    </main>
  ) : (
    <Shell active="catalog">{content}</Shell>
  );
}

export function Subscription({
  city,
  category,
}: {
  city: string;
  category: string;
}) {
  const [status, setStatus] = useState(""),
    [pendingCity, setPendingCity] = useState(""),
    [busy, setBusy] = useState(false),
    [saved, setSaved] = useState<{ id: string; delete_token: string } | null>(
      null,
    );
  useEffect(() => {
    try {
      const s = localStorage.getItem("ayda_subscription");
      if (s) setSaved(JSON.parse(s));
      const previous = localStorage.getItem("ayda_subscription_pending");
      if (!s && previous) {
        setPendingCity(JSON.parse(JSON.parse(previous).body).city);
        setStatus(
          "Предыдущий запрос мог сохраниться. Нажмите «Сохранить предпочтения», чтобы получить его результат без дубля. Будут использованы прежние параметры.",
        );
      }
    } catch {
      setStatus("Не удалось прочитать сохранённую подписку в этом браузере.");
    }
  }, []);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setStatus("");
    const fd = new FormData(e.currentTarget);
    let pending: { key: string; body: string };
    try {
      const existing = localStorage.getItem("ayda_subscription_pending");
      pending = existing
        ? JSON.parse(existing)
        : {
            key: crypto.randomUUID(),
            body: JSON.stringify({
              city: fd.get("city"),
              category: category || null,
              period: "week",
            }),
          };
      localStorage.setItem(
        "ayda_subscription_pending",
        JSON.stringify(pending),
      );
    } catch {
      setBusy(false);
      setStatus(
        "Браузер не разрешает сохранить ключ повтора. Разрешите локальное хранилище и повторите запрос.",
      );
      return;
    }
    try {
      const result = await api<{ id: string; delete_token: string }>(
        "/subscriptions",
        {
          method: "POST",
          headers: { "Idempotency-Key": pending.key },
          body: pending.body,
        },
      );
      setSaved(result);
      try {
        localStorage.setItem("ayda_subscription", JSON.stringify(result));
        localStorage.removeItem("ayda_subscription_pending");
      } catch {
        setStatus(
          "Подписка сохранена. Оставьте страницу открытой, чтобы сохранить возможность удаления.",
        );
        return;
      }
      setStatus(
        "Предпочтения сохранены. Доставка уведомлений появится после подключения канала связи.",
      );
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!saved) return;
    setBusy(true);
    try {
      await api(`/subscriptions/${saved.id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${saved.delete_token}` },
      });
      localStorage.removeItem("ayda_subscription");
      setSaved(null);
      setStatus("Подписка удалена.");
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="subscription">
      <h3>Ваш город. Ваши интересы.</h3>
      <p>Сохраните предпочтения для будущих подборок.</p>
      {saved ? (
        <button className="button secondary" disabled={busy} onClick={remove}>
          Удалить подписку
        </button>
      ) : (
        <form onSubmit={submit}>
          <label>
            Город
            <input
              name="city"
              placeholder="Например, Уфа"
              defaultValue={pendingCity || city}
              key={pendingCity || city}
              required
              maxLength={100}
            />
          </label>
          <button className="button" disabled={busy}>
            {busy ? "Сохраняем…" : "Сохранить предпочтения"}
            <ArrowRight size={17} />
          </button>
        </form>
      )}
      {status && (
        <p className="form-status" role="status">
          {status}
        </p>
      )}
      <small>
        Без рассылок, пока не подключён канал доставки. Удалить подписку можно в
        этом браузере.
      </small>
    </div>
  );
}
