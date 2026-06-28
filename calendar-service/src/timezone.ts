export function assertSupportedTimezone(timezone: string): void {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: timezone }).format(new Date());
  } catch {
    throw new Error(`Unsupported timezone: ${timezone}`);
  }
}

export function calendarCacheKey(prefix: string, query: Record<string, unknown>): string {
  const pairs = Object.keys(query)
    .sort()
    .map(key => `${key}=${String(query[key] ?? '')}`);
  return `${prefix}:${pairs.join('&')}`;
}
