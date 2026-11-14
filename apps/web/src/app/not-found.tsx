import Link from "next/link";
import { Shell } from "../components/ui";
export default function NotFound() {
  return <Shell><div className="inner-page"><h1>Страница не найдена</h1><p>Событие не опубликовано или такой страницы нет.</p><Link href="/" className="button">Вернуться к афише</Link></div></Shell>;
}
