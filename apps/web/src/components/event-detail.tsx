"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Event, date, price, statusName } from "../lib/api";
import editorialImages from "../lib/editorial-images.json";
import {
  ArrowUpRight,
  EventArtwork,
  ErrorNotice,
  EventCard,
  Shell,
  SourceLink,
} from "./ui";
export function EventDetail({ initialEvent }: { initialEvent: Event }) {
  const id = initialEvent.id;
  const description = (event: Event) =>
    (event.description || "")
      .replace(/Сеанс по официальной афише театра\.?/g, "")
      .replace(
        /Опубликованный тариф спектакля\.[\s\S]*?Источник тарифа:\s*https:\/\/rusdramteatr\.ru\/bilety\/?/g,
        "",
      )
      .trim();
  const [event, setEvent] = useState<Event | null>(initialEvent),
    [error, setError] = useState(""),
    [similar, setSimilar] = useState<Event[]>([]),
    [refresh, setRefresh] = useState(0);
  useEffect(() => {
    setError("");
    api<Event>(`/events/${encodeURIComponent(id)}`)
      .then((e) => {
        setEvent(e);
        api<{ items: Event[] }>(
          `/events?city=${encodeURIComponent(e.city)}&category=${encodeURIComponent(e.category)}&page_size=4`,
        )
          .then((r) =>
            setSimilar(r.items.filter((item) => item.id !== e.id).slice(0, 3)),
          )
          .catch(() => {});
      })
      .catch((e) => setError(e.message));
  }, [id, refresh]);
  return (
    <Shell active="catalog">
      <div className="inner-page">
        <Link href="/" className="back-link">
          ← Вернуться к афише
        </Link>
        {error ? (
          <ErrorNotice message={error} retry={() => setRefresh((v) => v + 1)} />
        ) : !event ? (
          <div className="skeleton">
            <div />
            <span />
            <span />
          </div>
        ) : (
          <>
            <article className="event-detail">
              <div className="detail-visual">
                <EventArtwork event={event} />
              </div>
              <div className="detail-info">
                <span
                  className={
                    event.status === "cancelled" ? "status cancelled" : "status"
                  }
                >
                  {statusName[event.status] || event.status}
                </span>
                <h1>{event.title}</h1>
                {event.verification_expired && (
                  <div className="notice" role="status">
                    <strong>Сведения требуют повторной проверки</strong>
                    <p>
                      Уточните актуальное расписание и билеты на странице
                      организатора.
                    </p>
                  </div>
                )}
                {event.status === "cancelled" && (
                  <div className="notice error" role="status">
                    Организатор отменил событие. Карточка сохранена для справки.
                  </div>
                )}
                {event.status === "rescheduled" && (
                  <div className="notice">
                    Событие перенесено. Проверьте новую дату у организатора.
                  </div>
                )}
                {event.ticket_availability &&
                  ["sold_out", "off_sale", "cancelled", "rescheduled"].includes(
                    event.ticket_availability,
                  ) && (
                    <div className="notice" role="status">
                      {event.ticket_availability === "sold_out"
                        ? "Билеты на этот сеанс закончились."
                        : event.ticket_availability === "cancelled"
                          ? "На билетной площадке сеанс отмечен как отменённый."
                          : event.ticket_availability === "rescheduled"
                            ? "На билетной площадке сеанс отмечен как перенесённый."
                            : "Продажа билетов на этот сеанс завершена."}{" "}
                      Он исключён из подборок. Карточка сохранена для справки.
                    </div>
                  )}
                <p>
                  {event.category}
                  {event.language ? ` · ${event.language}` : ""}
                </p>
                <dl className="detail-data">
                  <div>
                    <dt>Когда</dt>
                    <dd>{date(event.date_start, true)}</dd>
                  </div>
                  <div>
                    <dt>Билеты</dt>
                    <dd>{price(event)}</dd>
                  </div>
                  <div>
                    <dt>Где</dt>
                    <dd>
                      {event.city}
                      {event.venue ? `, ${event.venue}` : ""}
                      <small>{event.address || "Адрес уточняется"}</small>
                    </dd>
                  </div>
                  <div>
                    <dt>Возраст</dt>
                    <dd>
                      {event.age_limit == null
                        ? "Уточняется"
                        : `${event.age_limit}+`}
                      {event.duration_min != null && (
                        <small>{event.duration_min} мин.</small>
                      )}
                    </dd>
                  </div>
                </dl>
                {description(event) && (
                  <div className="detail-description">{description(event)}</div>
                )}
                {event.status !== "cancelled" &&
                  ![
                    "sold_out",
                    "off_sale",
                    "cancelled",
                    "rescheduled",
                  ].includes(event.ticket_availability || "") && (
                    <SourceLink href={event.org_link || event.source_url}>
                      {event.verification_expired
                        ? "Проверить у организатора"
                        : event.org_link
                          ? "Билеты у организатора"
                          : "Перейти к источнику"}
                    </SourceLink>
                  )}
                <section className="source-block">
                  <h2>Откуда информация</h2>
                  {event.image_source_url && (
                    <div className="source-item">
                      <SourceLink href={event.image_source_url}>
                        Изображение и сведения о билетах на Кассы.ру
                      </SourceLink>
                    </div>
                  )}
                  {(editorialImages as Record<string, { source: string }>)[
                    event.org_link || ""
                  ] && (
                    <div className="source-item">
                      <SourceLink
                        href={
                          (
                            editorialImages as Record<
                              string,
                              { source: string }
                            >
                          )[event.org_link || ""].source
                        }
                      >
                        Изображение на сайте театра
                      </SourceLink>
                    </div>
                  )}
                  {event.poster_url && event.poster_rights && (
                    <p>Права на постер: {event.poster_rights}</p>
                  )}
                  {event.ticket_links?.map((link) => (
                    <div key={link.url}>
                      <SourceLink href={link.url}>{link.label}</SourceLink>
                    </div>
                  ))}
                  {event.sources?.length ? (
                    event.sources.map((source, i) => (
                      <div className="source-item" key={`${source.url}-${i}`}>
                        <SourceLink href={source.url}>
                          {source.name || "Источник события"}
                        </SourceLink>
                      </div>
                    ))
                  ) : (
                    <SourceLink href={event.source_url}>
                      Первоисточник события
                    </SourceLink>
                  )}
                  <Link
                    className="text-button"
                    href={`/feedback?event_id=${encodeURIComponent(event.id)}`}
                  >
                    Сообщить об изменении <ArrowUpRight size={15} />
                  </Link>
                </section>
              </div>
            </article>
            {similar.length > 0 && (
              <section className="catalog-section" style={{ marginTop: 50 }}>
                <h2 style={{ marginBottom: 24 }}>Ещё в этом городе</h2>
                <div className="events-grid">
                  {similar.map((e) => (
                    <EventCard key={e.id} event={e} />
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </Shell>
  );
}
