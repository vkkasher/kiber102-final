/** Next standalone may use localhost internally while the browser uses 127.0.0.1. */
export function isAllowedOrigin(origin: string, requestOrigin: string, publicOrigin?: string) {
  function parse(value?: string) {
    try {
      const url = new URL(value || "");
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password && url.pathname === "/" && !url.search && !url.hash ? url : null;
    } catch { return null; }
  }
  const candidate = parse(origin), server = parse(requestOrigin), configured = parse(publicOrigin);
  if (!candidate || candidate.origin !== origin) return false;
  if (candidate.origin === server?.origin || candidate.origin === configured?.origin) return true;
  const loopback = new Set(["localhost", "127.0.0.1", "[::1]"]);
  return !!server && (loopback.has(server.hostname) || server.hostname === "0.0.0.0") && loopback.has(candidate.hostname) && candidate.protocol === server.protocol && candidate.port === server.port;
}
