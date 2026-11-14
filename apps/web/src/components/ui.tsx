"use client";
import Link from "next/link";
import {
  ArrowUpRight,
  Menu,
  MapPin,
  ArrowRight,
  CalendarDays,
  Search,
  Sparkles,
  X,
  ExternalLink,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Event, date, price, safeURL, statusName } from "../lib/api";
import editorialImages from "../lib/editorial-images.json";
export {
  ArrowUpRight,
  MapPin,
  ArrowRight,
  CalendarDays,
  Search,
  Sparkles,
  X,
  ExternalLink,
};

export function Header({ active = "" }: { active?: string }) {
  const [open, setOpen] = useState(false);
  const navigation = useRef<HTMLElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    navigation.current?.querySelector<HTMLAnchorElement>("a")?.focus();
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        trigger.current?.focus();
      }
    }
    addEventListener("keydown", escape);
    return () => removeEventListener("keydown", escape);
  }, [open]);
  return (
    <header className="site-header">
      <Link
        href="/"
        className="brand"
        aria-label="Афиша Башкортостана / Культура. На главную"
      >
        <span className="brand-word">
          Афиша<span className="brand-dot">.</span>
        </span>
      </Link>
      <div className="brand-caption">
        Культура
        <br />
        Башкортостана
      </div>
      <nav
        id="primary-navigation"
        ref={navigation}
        aria-label="Основная навигация"
        className={open ? "navigation open" : "navigation"}
      >
        <Link
          aria-current={active === "catalog" ? "page" : undefined}
          href="/events"
        >
          Афиша
        </Link>
        <Link
          aria-current={active === "agent" ? "page" : undefined}
          href="/agent"
        >
          ИИ-помощник
        </Link>
        <Link
          aria-current={active === "planner" ? "page" : undefined}
          href="/planner"
        >
          Планировщик дня
        </Link>
        <Link
          aria-current={active === "feedback" ? "page" : undefined}
          href="/feedback"
        >
          Предложить событие <ArrowUpRight size={15} />
        </Link>
      </nav>
      <span className="independent">Cicada 3301</span>
      <button
        className="menu-button"
        ref={trigger}
        aria-label={open ? "Закрыть меню" : "Открыть меню"}
        aria-controls="primary-navigation"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? <X /> : <Menu />}
      </button>
    </header>
  );
}

export function Footer() {
  const [clock, setClock] = useState<Date | null>(null);
  useEffect(() => {
    const tick = () => setClock(new Date());
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <footer className="site-footer">
      <div className="footer-brand">
        <Link
          href="/"
          className="brand"
          aria-label="Афиша Башкортостана / Культура"
        >
          <span className="brand-word">
            Афиша<span className="brand-dot">.</span>
          </span>
        </Link>
        <p>Культурный путеводитель по сценам, залам и музеям республики.</p>
        <small>Проект хакатона «Кибер102». Cicada 3301.</small>
      </div>
      <div className="footer-links">
        <Link href="/feedback">Обратная связь</Link>
        <Link href="/embed">Виджет для вашего сайта</Link>
        <a
          href="https://t.me/kiber102rfbot"
          target="_blank"
          rel="noopener noreferrer"
        >
          Telegram-бот
        </a>
        <a href="/api/docs" target="_blank" rel="noopener noreferrer">
          API и схема OpenAPI
        </a>
        <Link href="/admin">Вход для модератора</Link>
        <Link href="/feedback#terms">Условия использования</Link>
        <Link href="/feedback#privacy">Политика конфиденциальности</Link>
        <Link href="/credits">Материалы и авторы</Link>
      </div>
      <div className="footer-meta">
        <p className="local-time">
          Время всех сеансов указано по Башкортостану. Сейчас:{" "}
          <time dateTime={clock?.toISOString()}>
            {clock
              ? new Intl.DateTimeFormat("ru-RU", {
                  timeZone: "Asia/Yekaterinburg",
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                }).format(clock)
              : "…"}
          </time>
        </p>
      </div>
    </footer>
  );
}

export function TelegramAssistantLink() {
  return (
    <div className="telegram-assistant">
      <h3>Помощник в Telegram</h3>
      <p>Находите события в привычном чате.</p>
      <a
        href="https://t.me/kiber102rfbot"
        target="_blank"
        rel="noopener noreferrer"
      >
        Открыть бота <ArrowUpRight size={16} aria-hidden="true" />
      </a>
    </div>
  );
}

export function Shell({
  children,
  active,
}: {
  children: React.ReactNode;
  active?: string;
}) {
  return (
    <>
      <Header active={active} />
      <ConnectionNotice />
      <main id="content" aria-label="Основное содержимое">
        {children}
      </main>
      <Footer />
    </>
  );
}

export function EventArtwork({ event }: { event: Event }) {
  const [failedURL, setFailedURL] = useState<string | null>(null);
  const editorial = (
    editorialImages as Record<
      string,
      { url: string; source: string; caption: string; local_url?: string }
    >
  )[event.org_link || ""];
  const poster = safeURL(event.poster_url);
  const licensed = poster && event.poster_rights;
  const sessionImage = event.image_source_url
    ? safeURL(event.image_url)
    : undefined;
  const photograph = licensed
    ? poster
    : sessionImage || editorial?.local_url || editorial?.url;
  if (photograph && failedURL !== photograph)
    return (
      <figure className="event-photograph">
        <img
          className="event-poster"
          src={photograph}
          alt={
            licensed
              ? `Постер события «${event.title}»`
              : sessionImage
                ? `Изображение постановки «${event.title}»`
                : editorial
                  ? `Изображение постановки «${event.title}» с сайта театра`
                  : `Фото площадки: ${event.venue}`
          }
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailedURL(photograph)}
        />
      </figure>
    );
  const start = new Date(event.date_start);
  return (
    <div
      className="event-date-art"
      role="img"
      aria-label={`Дата события «${event.title}»`}
    >
      <span>{event.category}</span>
      <strong>
        {Number.isFinite(+start)
          ? new Intl.DateTimeFormat("ru-RU", {
              day: "2-digit",
              timeZone: "Asia/Yekaterinburg",
            }).format(start)
          : "?"}
      </strong>
      <span>
        {Number.isFinite(+start)
          ? new Intl.DateTimeFormat("ru-RU", {
              month: "long",
              timeZone: "Asia/Yekaterinburg",
            }).format(start)
          : "Дата уточняется"}
      </span>
    </div>
  );
}

export function EventCard({
  event: e,
  headingLevel = 3,
}: {
  event: Event;
  headingLevel?: 2 | 3;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <article className="event-card">
      <Link
        href={`/events/${encodeURIComponent(e.id)}`}
        className="event-visual"
      >
        <EventArtwork event={e} />
      </Link>
      <div className="event-body">
        <p className="event-kind">
          <span>{e.category}</span>
          <span>
            {e.age_limit == null ? "Возраст уточняется" : `${e.age_limit}+`}
          </span>
        </p>
        <Heading>
          <Link href={`/events/${encodeURIComponent(e.id)}`}>{e.title}</Link>
        </Heading>
        <p className="venue">
          {e.city}
          {e.venue ? `, ${e.venue}` : ""}
        </p>
        <div className="event-meta">
          <span>{date(e.date_start)}</span>
          <span>
            {e.age_limit == null ? "Возраст уточняется" : `${e.age_limit}+`}
          </span>
        </div>
        <div className="event-bottom">
          <strong>{price(e)}</strong>
          <span
            className={e.status === "cancelled" ? "status cancelled" : "status"}
          >
            {e.status === "published" && e.verified_at
              ? "Источник проверен"
              : statusName[e.status] || e.status}
          </span>
        </div>
      </div>
    </article>
  );
}

export function ErrorNotice({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="notice error" role="alert">
      <strong>Не удалось загрузить данные</strong>
      <p>{message}</p>
      {retry && (
        <button className="button secondary" onClick={retry}>
          Повторить загрузку
        </button>
      )}
    </div>
  );
}

export function SourceLink({
  href,
  children,
}: {
  href?: string | null;
  children: React.ReactNode;
}) {
  const url = safeURL(href);
  return url ? (
    <a
      className="source-link"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
    >
      {children}
      <ExternalLink size={15} />
    </a>
  ) : null;
}

export function ConnectionNotice() {
  const [offline, setOffline] = useState(false);
  useEffect(() => {
    const update = () => setOffline(!navigator.onLine);
    update();
    addEventListener("online", update);
    addEventListener("offline", update);
    return () => {
      removeEventListener("online", update);
      removeEventListener("offline", update);
    };
  }, []);
  return offline ? (
    <div className="connection-notice" role="status">
      Нет соединения с интернетом. Данные на экране могут устареть. Когда связь
      появится, обновите страницу.
    </div>
  ) : null;
}
