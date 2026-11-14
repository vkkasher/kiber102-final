"use client";
import { useRef, useState } from "react";
import { api } from "../lib/api";
import type { QueryResponse } from "../lib/generated-api";
import { pendingAction, subscriptionCapability, type PendingActionView } from "../lib/agent-reply";
import { EventCard } from "./ui";
export function CatalogAssistant() {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<Record<string, unknown>>({});
  const [reply, setReply] = useState<QueryResponse | null>(null);
  const [pending, setPending] = useState<PendingActionView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef<string | undefined>(undefined);
  async function ask(message: string, confirmation_nonce?: string) {
    if (busy || !message.trim()) return;
    setBusy(true); setError("");
    try {
      const result = await api<QueryResponse>("/agent/query", { method: "POST", body: JSON.stringify({ message, state, dialog_id: dialog.current, confirmation_nonce }) });
      setReply(result); setState(result.state); dialog.current = result.dialog_id || undefined; setPending(pendingAction(result.pending_action));
      const capability = subscriptionCapability(result.action_result);
      if (capability) localStorage.setItem("ayda_subscription", JSON.stringify(capability));
    } catch(e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <details className="planner"><summary>Описать пожелания своими словами</summary><form onSubmit={e => { e.preventDefault(); ask(query); }}><label>Пожелания к событиям<textarea value={query} onChange={e => setQuery(e.target.value)} required maxLength={2000} placeholder="В Уфе на выходных с ребёнком 8 лет до 1000 рублей" /></label><button className="button" disabled={busy}>{busy ? "Ищем…" : "Подобрать события"}</button></form>{error && <p role="alert">{error}</p>}{reply && <div aria-live="polite"><p>{reply.text}</p>{reply.question && !reply.text.includes(reply.question) && <p>{reply.question}</p>}<div className="events-grid">{reply.events.map(event => <div key={event.id}><EventCard event={event}/>{reply.explanations?.[event.id] && <p>{reply.explanations[event.id]}</p>}</div>)}</div></div>}{pending && <section className="notice" aria-label="Подтверждение действия"><p>{pending.tool === "subscribe" ? "Сохранить подписку?" : "Передать обращение модератору?"}</p><dl>{Object.entries(pending.arguments).map(([key,value]) => <div key={key}><dt>{({city:"Город",category:"Категория",period:"Период",message:"Сообщение",event_id:"Событие"} as Record<string,string>)[key] || key}</dt><dd>{String(value ?? "Не указано")}</dd></div>)}</dl><div className="button-row"><button className="button" disabled={busy} onClick={() => ask("Подтверждаю", pending.nonce)}>Подтвердить</button><button className="button secondary" disabled={busy} onClick={() => setPending(null)}>Отменить действие</button></div></section>}</details>;
}
