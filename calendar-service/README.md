# Financial Calendar Service

This static site consumes the shared `financial-calendar-service` export at
`assets/calendar_events.json`. The long-term service shape is:

Data sources -> adapters -> raw payloads -> normalizer -> deduplication ->
PostgreSQL -> Calendar API -> cache -> Lynus / Charles tenants.

The browser never calls external vendors directly and must not contain vendor
API keys. Taiwan ex-rights, ex-dividend, Taiwan macro, Taiwan CPI, Taiwan GDP,
Taiwan import/export, and Taiwan central bank rate decision crawlers are
explicitly excluded.

## API contract

- `GET /api/calendar/events`
- `GET /api/calendar/upcoming`
- `GET /api/calendar/symbol/:symbol`
- `GET /api/calendar/ics`

Query keys include `from`, `to`, `markets`, `types`, `importance`, `symbols`,
`tenant`, `timezone`, `limit`, and `cursor`.

## Cache

- upcoming: 5-15 minutes
- events: 5-30 minutes
- symbol: 15-60 minutes
- ics: 30-60 minutes

Crawler updates should purge affected keys by tenant, market, date range, type,
and symbol.

## Environment placeholders

```env
DATABASE_URL=
REDIS_URL=
FINANCIAL_DATA_API_KEY=
GLOBAL_MACRO_API_KEY=
```
