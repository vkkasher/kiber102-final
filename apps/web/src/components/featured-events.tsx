"use client";
import Link from "next/link";
import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Event, price } from "../lib/api";
import editorialImages from "../lib/editorial-images.json";
import { ArrowUpRight, EventArtwork } from "./ui";

export function FeaturedEvents({
  events,
  loading,
}: {
  events: Event[];
  loading: boolean;
}) {
  const [slide, setSlide] = useState(0);
  const images: Record<string, unknown> = editorialImages;
  const seen = new Set<string>();
  const featured = events
    .filter((event) => {
      if (
        (!event.image_url && !images[event.org_link || ""]) ||
        seen.has(event.normalized_title)
      )
        return false;
      seen.add(event.normalized_title);
      return true;
    })
    .sort(
      (a, b) =>
        Number(b.venue?.includes("оперы")) - Number(a.venue?.includes("оперы")),
    )
    .slice(0, 5);
  if (loading && !featured.length)
    return (
      <div
        className="featured-loading"
        role="status"
        aria-label="Загружаем ближайшие события"
      >
        <h1 className="sr-only">Культура, которая рядом</h1>
      </div>
    );
  if (!featured.length)
    return (
      <section className="discovery-intro">
        <h1>Культура, которая рядом</h1>
      </section>
    );
  const index = slide % featured.length;
  const event = featured[index];
  const start = new Date(event.date_start);
  const time = new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Yekaterinburg",
  }).format(start);
  return (
    <section
      className="home-feature"
      aria-label="Ближайшее на сцене"
      aria-roledescription="карусель"
    >
      <div className="home-feature-copy">
        <p>
          {new Intl.DateTimeFormat("ru-RU", {
            day: "numeric",
            month: "long",
            timeZone: "Asia/Yekaterinburg",
          }).format(start)}{" "}
          · {event.city}
        </p>
        <h1>
          <span>Культура,</span>
          <span>которая рядом</span>
        </h1>
        <span>Театры, музыка и встречи Башкортостана</span>
      </div>
      <Link
        className="home-feature-image"
        href={`/events/${encodeURIComponent(event.id)}`}
        aria-label={`Подробнее: ${event.title}`}
      >
        <EventArtwork event={event} />
      </Link>
      <div className="home-event-strip" aria-live="polite">
        <div>
          <h2>
            <Link href={`/events/${encodeURIComponent(event.id)}`}>
              {event.title}
            </Link>
          </h2>
          <p>
            {event.category} ·{" "}
            {event.age_limit == null
              ? "Возраст уточняется"
              : `${event.age_limit}+`}{" "}
            · {event.venue}
          </p>
        </div>
        <div className="home-event-time">
          <strong>{time}</strong>
          <span>{price(event)}</span>
        </div>
        <Link
          className="button"
          href={`/events/${encodeURIComponent(event.id)}`}
        >
          Подробнее <ArrowUpRight size={18} />
        </Link>
        <div className="home-feature-controls">
          <button
            aria-label="Предыдущее событие"
            onClick={() =>
              setSlide((index - 1 + featured.length) % featured.length)
            }
          >
            <ChevronLeft size={18} />
          </button>
          <span>
            {index + 1}/{featured.length}
          </span>
          <button
            aria-label="Следующее событие"
            onClick={() => setSlide((index + 1) % featured.length)}
          >
            <ChevronRight size={18} />
          </button>
        </div>
      </div>
    </section>
  );
}
