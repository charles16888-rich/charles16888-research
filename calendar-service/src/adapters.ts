import { CalendarSourceAdapter, NormalizedCalendarEvent, RawCalendarRecord } from './types';

export abstract class BaseCalendarAdapter implements CalendarSourceAdapter {
  constructor(public id: string) {}
  abstract fetchRaw(): Promise<RawCalendarRecord[]>;
  abstract normalize(raw: RawCalendarRecord): Promise<NormalizedCalendarEvent[]>;

  validate(event: NormalizedCalendarEvent): void {
    if (!event.sourceId || !event.market || !event.eventType || !event.title) {
      throw new Error(`Invalid calendar event from ${this.id}`);
    }
    if (event.market === 'TW' && ['tw_ex_rights', 'tw_ex_dividend', 'tw_dividend'].includes(event.eventType)) {
      throw new Error('Excluded Taiwan ex-rights/ex-dividend event rejected');
    }
  }
}

export class BlsCalendarAdapter extends BaseCalendarAdapter {
  async fetchRaw(): Promise<RawCalendarRecord[]> { return []; }
  async normalize(_raw: RawCalendarRecord): Promise<NormalizedCalendarEvent[]> { return []; }
}

export class BeaCalendarAdapter extends BaseCalendarAdapter {
  async fetchRaw(): Promise<RawCalendarRecord[]> { return []; }
  async normalize(_raw: RawCalendarRecord): Promise<NormalizedCalendarEvent[]> { return []; }
}

export class FedFomcCalendarAdapter extends BaseCalendarAdapter {
  async fetchRaw(): Promise<RawCalendarRecord[]> { return []; }
  async normalize(_raw: RawCalendarRecord): Promise<NormalizedCalendarEvent[]> { return []; }
}

export class UsEarningsVendorAdapter extends BaseCalendarAdapter {
  async fetchRaw(): Promise<RawCalendarRecord[]> {
    if (!process.env.FINANCIAL_DATA_API_KEY) return [];
    return [];
  }
  async normalize(_raw: RawCalendarRecord): Promise<NormalizedCalendarEvent[]> { return []; }
}

export class TwMaterialNewsAdapter extends BaseCalendarAdapter {
  async fetchRaw(): Promise<RawCalendarRecord[]> { return []; }
  async normalize(_raw: RawCalendarRecord): Promise<NormalizedCalendarEvent[]> { return []; }
}

export class TwCompanyAnnouncementAdapter extends BaseCalendarAdapter {
  async fetchRaw(): Promise<RawCalendarRecord[]> { return []; }
  async normalize(_raw: RawCalendarRecord): Promise<NormalizedCalendarEvent[]> { return []; }
}

export class TwMarketHolidaysAdapter extends BaseCalendarAdapter {
  async fetchRaw(): Promise<RawCalendarRecord[]> { return []; }
  async normalize(_raw: RawCalendarRecord): Promise<NormalizedCalendarEvent[]> { return []; }
}
