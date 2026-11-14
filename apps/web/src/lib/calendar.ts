/** Pure calendar operations in the event region, independent of browser timezone. */
export function localDay(offset = 0, now = new Date()) {
  return new Date(now.getTime() + 5 * 60 * 60 * 1000 + offset * 86400000)
    .toISOString().slice(0, 10);
}
export function periodRange(period: string, now = new Date()) {
  const today = localDay(0, now);
  const weekday = new Date(`${today}T12:00:00Z`).getUTCDay();
  if (period === "weekend") {
    const offset = weekday === 0 || weekday === 6 ? 0 : 6 - weekday;
    return { date_from: localDay(offset, now), date_to: localDay(offset + (weekday === 0 ? 0 : 1), now) };
  }
  const offset = period === "tomorrow" ? 1 : 0;
  return { date_from: localDay(offset, now), date_to: localDay(period === "week" ? 6 : offset, now) };
}
