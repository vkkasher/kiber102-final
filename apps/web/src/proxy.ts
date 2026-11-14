import { NextRequest, NextResponse } from "next/server";
/** Establish identity before the client starts parallel API calls. */
export function proxy(request: NextRequest) {
  const response = NextResponse.next();
  if (!/^[a-f0-9-]{36}$/.test(request.cookies.get("ayda_visitor")?.value || "")) {
    response.cookies.set("ayda_visitor", crypto.randomUUID(), { httpOnly: true, sameSite: "lax", secure: (process.env.WEB_PUBLIC_URL || "").startsWith("https:"), path: "/", maxAge: 31536000 });
    response.headers.set("Cache-Control", "private, no-store");
  }
  return response;
}
export const config = { matcher: ["/", "/agent", "/feedback", "/admin", "/events/:path*"] };
