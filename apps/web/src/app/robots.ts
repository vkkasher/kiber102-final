import type { MetadataRoute } from "next";
import { publicURL } from "../lib/server-api";
export default function robots(): MetadataRoute.Robots { return { rules: { userAgent: "*", allow: "/", disallow: ["/admin", "/api/", "/agent", "/feedback", "/embed", "/widget"] }, sitemap: new URL("/sitemap.xml", publicURL()).href }; }
