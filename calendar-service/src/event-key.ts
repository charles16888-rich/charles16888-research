import { NormalizedCalendarEvent } from './types';

const aliases: Array<[RegExp, string]> = [
  [/consumer price index/gi, 'cpi'],
  [/fomc rate decision|fed interest rate decision/gi, 'fomc-rate-decision'],
  [/nonfarm payrolls|employment situation/gi, 'nfp'],
];

export function normalizeTitle(title: string): string {
  let out = title.toLowerCase();
  for (const [pattern, value] of aliases) out = out.replace(pattern, value);
  return out.replace(/[^\w\s-]/g, '').replace(/\s+/g, '-').slice(0, 72) || 'event';
}

export function generateEventKey(sourceGroup: string, event: NormalizedCalendarEvent): string {
  const symbolOrCountry = event.symbol || event.country || event.market;
  return [
    sourceGroup,
    event.eventType,
    event.market,
    symbolOrCountry,
    event.period || '',
    event.eventDateLocal,
    normalizeTitle(event.title),
  ].join(':');
}
