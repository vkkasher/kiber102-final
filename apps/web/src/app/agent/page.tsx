"use client";
import { useEffect, useRef, useState } from "react";
import { api, Event } from "../../lib/api";
import type { QueryResponse } from "../../lib/generated-api";
import {
  pendingAction,
  subscriptionCapability,
  type PendingActionView,
} from "../../lib/agent-reply";
import {
  ArrowRight,
  ErrorNotice,
  EventCard,
  Shell,
  TelegramAssistantLink,
} from "../../components/ui";
import { Planner } from "../../components/planner";
type Message = {
  role: "user" | "assistant";
  content: string;
  events?: Event[];
  mode?: string;
  trace_id?: string;
  explanations?: Record<string, string>;
};
export default function Agent() {
  const [messages, setMessages] = useState<Message[]>([]),
    [message, setMessage] = useState(""),
    [state, setState] = useState<Record<string, unknown>>({}),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [offline, setOffline] = useState(false),
    [pending, setPending] = useState<PendingActionView | null>(null);
  const dialog = useRef<string | undefined>(undefined);
  const nonce = useRef<string | undefined>(undefined);
  const end = useRef<HTMLDivElement>(null);
  const last = useRef("");
  useEffect(() => {
    try {
      const saved = localStorage.getItem("ayda_agent_pending");
      if (saved) {
        const value = JSON.parse(saved);
        if (Date.parse(value.pending.expires_at) > Date.now()) {
          setPending(pendingAction(value.pending));
          setState(value.state);
          dialog.current = value.dialog;
        } else localStorage.removeItem("ayda_agent_pending");
      }
    } catch {
      localStorage.removeItem("ayda_agent_pending");
    }
  }, []);
  useEffect(() => {
    if (messages.length)
      end.current?.scrollIntoView({
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "nearest",
      });
  }, [messages]);
  async function ask(text: string, confirmation?: string) {
    if (!text.trim() || busy) return;
    last.current = text;
    nonce.current = confirmation;
    setError("");
    const history = messages.map(({ role, content }) => ({ role, content }));
    setMessages((m) => [...m, { role: "user", content: text }]);
    setMessage("");
    setBusy(true);
    try {
      const reply = await api<QueryResponse>("/agent/query", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          history: history.slice(-12),
          state,
          offline,
          dialog_id: dialog.current,
          confirmation_nonce: confirmation,
        }),
      });
      setState(reply.state);
      dialog.current = reply.dialog_id || dialog.current;
      const prepared = pendingAction(reply.pending_action);
      setPending(prepared);
      if (prepared)
        localStorage.setItem(
          "ayda_agent_pending",
          JSON.stringify({
            pending: prepared,
            state: reply.state,
            dialog: dialog.current,
          }),
        );
      else localStorage.removeItem("ayda_agent_pending");
      const capability = subscriptionCapability(reply.action_result);
      if (capability)
        localStorage.setItem("ayda_subscription", JSON.stringify(capability));
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content:
            reply.text +
            (reply.question && !reply.text.includes(reply.question)
              ? `\n\n${reply.question}`
              : ""),
          events: reply.events,
          mode: reply.mode,
          trace_id: reply.trace_id,
          explanations: reply.explanations,
        },
      ]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Shell active="agent">
      <div className="inner-page agent-page">
        <div className="page-intro">
          <h1>
            Какого дня
            <br />
            вам хочется?
          </h1>
          <p>
            Расскажите о планах, бюджете и компании. Помощник подберёт события
            из проверенной афиши или уточнит детали.
          </p>
        </div>
        <div className="chat-layout">
          <aside className="chat-aside">
            <h2>Начните с идеи</h2>
            <p>
              Можно писать своими словами и уточнять пожелания по ходу
              разговора.
            </p>
            {[
              "Куда сходить с ребёнком в Уфе?",
              "Что есть бесплатно на выходных?",
              "Хочу в театр на этой неделе",
            ].map((text) => (
              <button
                className="chip"
                disabled={busy}
                key={text}
                onClick={() => ask(text)}
              >
                {text}
              </button>
            ))}
            <button
              className="text-button"
              disabled={busy}
              onClick={() => {
                setMessages([]);
                setState({});
                setError("");
                setPending(null);
                dialog.current = undefined;
                localStorage.removeItem("ayda_agent_pending");
              }}
            >
              Начать заново
            </button>
            <label className="chat-options">
              <input
                type="checkbox"
                checked={offline}
                onChange={(e) => setOffline(e.target.checked)}
              />
              Поиск без языковой модели
            </label>
            <TelegramAssistantLink />
          </aside>
          <div className="chat-main">
            <div className="chat-messages" aria-live="polite" aria-busy={busy}>
              {messages.length === 0 && (
                <div className="chat-message assistant-welcome">
                  <h2>Начнём с ваших планов</h2>
                  <p>
                    В каком городе ищем события? Если идёте с ребёнком, укажите
                    его возраст. Можно сразу добавить дату и бюджет.
                  </p>
                  <div className="assistant-suggestions">
                    <button
                      disabled={busy}
                      onClick={() => ask("Хочу в театр на этой неделе в Уфе")}
                    >
                      Вечер в театре <ArrowRight size={16} />
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        ask("Куда сходить с ребёнком в Стерлитамаке?")
                      }
                    >
                      Семейный выходной <ArrowRight size={16} />
                    </button>
                  </div>
                </div>
              )}
              {messages.map((m, i) => (
                <article className={`chat-message ${m.role}`} key={i}>
                  <strong>{m.role === "user" ? "Вы" : "Помощник"}</strong>
                  <p>{m.content}</p>
                  {m.role === "assistant" &&
                    m.content.includes("нет полной цены") &&
                    !m.events?.length && (
                      <button
                        className="text-button"
                        disabled={busy || i !== messages.length - 1}
                        onClick={() =>
                          ask("Покажи события без ограничений по бюджету")
                        }
                      >
                        Показать события без ценового фильтра{" "}
                        <ArrowRight size={16} />
                      </button>
                    )}
                  {!!m.events?.length && (
                    <div className="events-grid">
                      {m.events.map((e) => (
                        <div key={e.id}>
                          <EventCard event={e} />
                          {m.explanations?.[e.id] && (
                            <p className="event-reason">
                              {m.explanations[e.id]}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {m.mode && (
                    <small>
                      {m.mode === "fallback"
                        ? "Ответ без языковой модели"
                        : "Ответ с языковой моделью"}
                      . Факты из каталога.
                    </small>
                  )}
                </article>
              ))}
              {busy && (
                <p className="form-status" role="status">
                  Сверяем пожелания с афишей…
                </p>
              )}
              <div ref={end} />
            </div>
            {error && (
              <ErrorNotice
                message={error}
                retry={() => ask(last.current, nonce.current)}
              />
            )}
            {pending && (
              <section className="notice" aria-label="Подтверждение действия">
                <h2>Подтвердите действие</h2>
                <p>
                  {pending.tool === "subscribe"
                    ? "Сохранить подписку с этими параметрами?"
                    : "Передать обращение модератору?"}
                </p>
                <dl>
                  {Object.entries(pending.arguments).map(([key, value]) => (
                    <div key={key}>
                      <dt>
                        {(
                          {
                            city: "Город",
                            category: "Категория",
                            period: "Период",
                            message: "Сообщение",
                            event_id: "Событие",
                          } as Record<string, string>
                        )[key] || key}
                      </dt>
                      <dd>{String(value ?? "Не указано")}</dd>
                    </div>
                  ))}
                </dl>
                <div className="button-row">
                  <button
                    className="button"
                    disabled={busy}
                    onClick={() => ask("Подтверждаю", pending.nonce)}
                  >
                    Подтвердить
                  </button>
                  <button
                    className="button secondary"
                    disabled={busy}
                    onClick={() => {
                      setPending(null);
                      localStorage.removeItem("ayda_agent_pending");
                    }}
                  >
                    Отменить действие
                  </button>
                </div>
              </section>
            )}
            <div className="chat-composer">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  ask(message);
                }}
              >
                <label className="sr-only" htmlFor="agent-message">
                  Ваш запрос
                </label>
                <textarea
                  id="agent-message"
                  placeholder="Например, куда пойти в Уфе в субботу до 1000 ₽"
                  maxLength={2000}
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      ask(message);
                    }
                  }}
                />
                <button className="button" disabled={busy || !message.trim()}>
                  Найти <ArrowRight size={18} />
                </button>
              </form>
              <small>
                Помощник не бронирует билеты. Проверьте расписание у
                организатора. Enter отправляет, Shift + Enter переносит строку.
              </small>
            </div>
            <Planner />
          </div>
        </div>
      </div>
    </Shell>
  );
}
