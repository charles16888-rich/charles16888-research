export type CalendarMarket = 'US' | 'TW' | 'GLOBAL';
export type CalendarStatus = 'scheduled' | 'released' | 'revised' | 'cancelled' | 'tentative' | 'postponed';
export type EventImportance = 1 | 2 | 3;

export type CalendarEventType =
  | 'macro'
  | 'earnings'
  | 'earnings_call'
  | 'dividend'
  | 'ex_dividend'
  | 'payment_date'
  | 'stock_split'
  | 'ipo'
  | 'shareholder_meeting'
  | 'investor_conference'
  | 'material_news'
  | 'company_announcement'
  | 'market_holiday'
  | 'half_trading_day'
  | 'central_bank'
  | 'fed_speech'
  | 'other';

export type RawCalendarRecord = {
  sourceId: string;
  sourceUrl?: string;
  fetchedAt: string;
  payload: unknown;
};

export type NormalizedCalendarEvent = {
  sourceId: string;
  sourceUrl?: string;
  market: CalendarMarket;
  country?: string;
  exchange?: string;
  symbol?: string;
  symbolName?: string;
  eventType: CalendarEventType;
  category?: string;
  title: string;
  description?: string;
  eventDateLocal: string;
  eventTimeLocal?: string;
  timezone: string;
  eventTimeUtc?: string;
  isAllDay: boolean;
  isTimeConfirmed: boolean;
  period?: string;
  actual?: string;
  forecast?: string;
  previous?: string;
  unit?: string;
  importance: EventImportance;
  status: CalendarStatus;
  sourcePublishedAt?: string;
  lastSeenAt: string;
  rawHash: string;
  metadata?: Record<string, unknown>;
};

export interface CalendarSourceAdapter {
  id: string;
  fetchRaw(): Promise<RawCalendarRecord[]>;
  normalize(raw: RawCalendarRecord): Promise<NormalizedCalendarEvent[]>;
  validate(event: NormalizedCalendarEvent): void;
}
