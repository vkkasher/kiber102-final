"use client";
import { FormEvent, useState, useEffect } from "react";
import { api } from "../../lib/api";
import { Shell, ArrowRight } from "../../components/ui";
import type { FeedbackInput, FeedbackOutput } from "../../lib/generated-api";
const pendingKey = "afisha-feedback-pending";
type PendingFeedback = { key: string; body: FeedbackInput };
export default function Feedback() {
  const [status, setStatus] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [eventId, setEventId] = useState(""),
    [message, setMessage] = useState(""),
    [contact, setContact] = useState(""),
    [pending, setPending] = useState<PendingFeedback | null>(null);
  useEffect(() => {
    setEventId(new URLSearchParams(location.search).get("event_id") || "");
    try {
      const saved: PendingFeedback | null = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
      if (saved && typeof saved.key === "string" && /^[a-zA-Z0-9_-]{16,200}$/.test(saved.key) && typeof saved.body?.message === "string" && saved.body.message.length <= 5000 && (saved.body.contact == null || typeof saved.body.contact === "string")) {
        setPending(saved);
        setMessage(saved.body.message);
        setContact(saved.body.contact || "");
        setEventId(saved.body.event_id || "");
      }
    } catch { /* A blocked storage is reported before the next submission. */ }
  }, []);
  function newMessage() {
    try { sessionStorage.removeItem(pendingKey); } catch { setError("Не удалось очистить незавершённую отправку в этой вкладке"); return; }
    setPending(null);
    setError("");
  }
  async function send(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setStatus("");
    setError("");
    const form = e.currentTarget;
    const fd = new FormData(form);
    try {
      const request = pending || { key: crypto.randomUUID(), body: {
        message,
        contact: contact || null,
        event_id: eventId || null,
        website: String(fd.get("website") || ""),
      } };
      try { sessionStorage.setItem(pendingKey, JSON.stringify(request)); }
      catch { throw new Error("Разрешите хранение данных в этой вкладке, чтобы безопасно повторить отправку"); }
      setPending(request);
      const r = await api<FeedbackOutput>("/feedback", {
        method: "POST",
        headers: { "Idempotency-Key": request.key },
        body: JSON.stringify(request.body),
      });
      sessionStorage.removeItem(pendingKey);
      setPending(null);
      setMessage("");
      setContact("");
      setStatus(
        `Сообщение получено и передано в очередь модерации. Номер обращения ${r.id}.`,
      );
      form.reset();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Shell>
      <div className="inner-page">
        <div className="page-intro">
          <h1>
            Давайте сделаем
            <br />
            афишу лучше.
          </h1>
          <p>Предложите событие, сообщите об изменении или напишите нам.</p>
        </div>
        <div className="form-layout">
          <form className="form-panel" onSubmit={send}>
            <label>
              Ваше сообщение
              <textarea
                name="message"
                aria-label="Ваше сообщение"
                required
                minLength={10}
                maxLength={5000}
                placeholder="Если предлагаете событие, добавьте ссылку на страницу организатора, город и дату."
                rows={7}
                value={message}
                onChange={e => setMessage(e.target.value)}
                readOnly={!!pending}
              />
            </label>
            <label>
              Контакт для ответа, необязательно
              <input
                name="contact"
                aria-label="Контакт для ответа, необязательно"
                maxLength={250}
                placeholder="Почта или Telegram"
                autoComplete="email"
                value={contact}
                onChange={e => setContact(e.target.value)}
                readOnly={!!pending}
              />
            </label>
            <label className="honeypot" aria-hidden="true">
              Ваш сайт
              <input tabIndex={-1} name="website" autoComplete="off" />
            </label>
            {eventId && <small>Обращение относится к событию {eventId}.</small>}
            {pending && <div className="notice"><p>Ответ на отправку ещё не получен. Повтор вернёт тот же номер обращения. Текст и контакт сохранены в этой вкладке до получения ответа или её закрытия.</p><button className="text-button" type="button" onClick={newMessage} disabled={busy}>Изменить сообщение</button><small>Предыдущая отправка уже могла попасть к модератору.</small></div>}
            <p>
              <small>
                Нажимая «Отправить сообщение», вы передаёте текст и указанный
                контакт модератору. Не отправляйте платёжные данные и другую
                чувствительную информацию.
              </small>
            </p>
            <button className="button" disabled={busy}>
              {busy ? "Отправляем…" : pending ? "Повторить отправку" : "Отправить сообщение"}
              <ArrowRight size={17} />
            </button>
            {status && (
              <div className="notice" role="status">
                {status}
              </div>
            )}
            {error && (
              <div className="notice error" role="alert">
                {error}. Попробуйте ещё раз.
              </div>
            )}
          </form>
          <aside className="form-aside">
            <h2>
              Хорошая афиша
              <br />
              начинается с источника.
            </h2>
            <p>
              Нам важна ссылка на официальный сайт, страницу площадки или
              организатора. После проверки событие сможет появиться в каталоге.
            </p>
            <p>
              Предложение не публикуется автоматически. Ответ и сроки
              рассмотрения не гарантируются.
            </p>
            <p>
              Сервис независимый. Здесь нельзя купить билет или
              зарегистрироваться на событие. Эти действия доступны только у
              организатора.
            </p>
          </aside>
        </div>
        <section id="terms" className="notice" style={{ marginTop: "32px" }}>
          <h2>Условия использования</h2>
          <p>
            Сервис предоставляет информационные сведения о культурных событиях Республики Башкортостан
            на основе проверенных публичных источников. Продажа билетов и бронирование мест производятся
            непосредственно на сайтах организаторов и билетных операторов.
          </p>
        </section>
        <section id="privacy" className="notice" style={{ marginTop: "24px" }}>
          <h2>Политика конфиденциальности</h2>
          <p>
            Сервис не передаёт персональные данные посетителей сторонним лицам. Личные адреса и геолокация
            пользователей исключены из обработки языковыми моделями в строгом соответствии с требованиями безопасности.
          </p>
        </section>
      </div>
    </Shell>
  );
}
