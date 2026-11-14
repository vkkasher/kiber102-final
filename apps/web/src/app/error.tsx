"use client";
import { Shell, ErrorNotice } from "../components/ui";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return <Shell><div className="inner-page"><h1>Не удалось открыть страницу</h1><ErrorNotice message="Сервис афиши временно недоступен" retry={reset} /></div></Shell>;
}
