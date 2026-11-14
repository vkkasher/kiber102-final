import { EventDetail } from "../../../components/event-detail";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { publicEvent } from "../../../lib/server-api";
export const dynamic = "force-dynamic";
export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  const event = await publicEvent(id);
  if (!event) return { title: "Событие не найдено", robots: { index: false } };
  const title = `${event.status === "cancelled" ? "Отменено: " : ""}${event.title}`;
  const description = `${event.city}. ${event.description || event.category}`.slice(0, 240);
  return { title, description, alternates: { canonical: `/events/${encodeURIComponent(id)}` }, openGraph: { title, description, locale: "ru_RU", type: "website" }, twitter: { card: "summary", title, description } };
}
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const event = await publicEvent((await params).id);
  if (!event) notFound();
  return <EventDetail initialEvent={event} />;
}
