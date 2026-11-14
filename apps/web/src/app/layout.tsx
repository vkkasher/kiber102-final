import type { Metadata, Viewport } from "next";
import "@fontsource-variable/manrope";
import "./globals.css";
import { publicURL } from "../lib/server-api";
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#ffffff",
};
export const metadata: Metadata = {
  metadataBase: publicURL(),
  alternates: { canonical: "/" },
  title: {
    default: "Афиша Башкортостана / Культура",
    template: "%s | Афиша Башкортостана / Культура",
  },
  description:
    "Независимая афиша культуры Башкортостана. События, проверенные источники и планы на выходные.",
  openGraph: {
    title: "Афиша культуры Башкортостана",
    description: "Найдите культурные события Башкортостана",
    locale: "ru_RU",
    type: "website",
  },
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <a className="skip" href="#content">
          К содержимому
        </a>
        {children}
      </body>
    </html>
  );
}
