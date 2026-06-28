CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE calendar_sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  market TEXT,
  country TEXT,
  base_url TEXT,
  license_note TEXT,
  attribution_text TEXT,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  crawl_schedule TEXT,
  timeout_seconds INTEGER DEFAULT 30,
  rate_limit_per_minute INTEGER DEFAULT 30,
  priority INTEGER DEFAULT 50,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE financial_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_key TEXT NOT NULL UNIQUE,
  source_id TEXT NOT NULL REFERENCES calendar_sources(id),
  market TEXT NOT NULL CHECK (market IN ('US', 'TW', 'GLOBAL')),
  country TEXT,
  exchange TEXT,
  symbol TEXT,
  symbol_name TEXT,
  event_type TEXT NOT NULL,
  category TEXT,
  title TEXT NOT NULL,
  description TEXT,
  event_date_local DATE NOT NULL,
  event_time_local TIME,
  timezone TEXT NOT NULL,
  event_time_utc TIMESTAMPTZ,
  is_all_day BOOLEAN NOT NULL DEFAULT FALSE,
  is_time_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
  period TEXT,
  actual TEXT,
  forecast TEXT,
  previous TEXT,
  unit TEXT,
  importance INTEGER NOT NULL DEFAULT 1 CHECK (importance BETWEEN 1 AND 3),
  status TEXT NOT NULL DEFAULT 'scheduled',
  source_url TEXT,
  source_published_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  raw_hash TEXT,
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE event_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id UUID NOT NULL REFERENCES financial_events(id) ON DELETE CASCADE,
  old_payload JSONB,
  new_payload JSONB,
  changed_fields TEXT[],
  detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE raw_calendar_payloads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id TEXT NOT NULL REFERENCES calendar_sources(id),
  source_url TEXT,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  raw_hash TEXT NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE crawl_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id TEXT NOT NULL REFERENCES calendar_sources(id),
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ended_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  records_read INTEGER DEFAULT 0,
  records_inserted INTEGER DEFAULT 0,
  records_updated INTEGER DEFAULT 0,
  records_skipped INTEGER DEFAULT 0,
  error_message TEXT,
  error_stack TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_financial_events_date ON financial_events(event_date_local);
CREATE INDEX idx_financial_events_market_date ON financial_events(market, event_date_local);
CREATE INDEX idx_financial_events_symbol_date ON financial_events(symbol, event_date_local);
CREATE INDEX idx_financial_events_type_date ON financial_events(event_type, event_date_local);
CREATE INDEX idx_financial_events_importance_date ON financial_events(importance, event_date_local);
CREATE INDEX idx_financial_events_event_time_utc ON financial_events(event_time_utc);
CREATE INDEX idx_financial_events_metadata_gin ON financial_events USING GIN(metadata);
