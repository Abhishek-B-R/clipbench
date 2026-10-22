-- /api/try (the /try playground). Counts and costs only: no audio, no transcript, no raw IP.

-- Per-visitor limits. key = "<scope>:" + sha256 of the client IP (never the IP itself); window_start is unix
-- seconds of the fixed window (10 minutes or one UTC day). Old rows are deleted by the scheduled handler.
CREATE TABLE try_limits (
  key TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (key, window_start)
);

-- Global spend per UTC day (YYYY-MM-DD). committed_usd = spent_usd plus the reservations of runs in flight; a
-- run reserves the estimated cost of every provider first and is refused when that would pass the daily budget.
CREATE TABLE try_budget (
  day TEXT PRIMARY KEY,
  committed_usd REAL NOT NULL DEFAULT 0,
  spent_usd REAL NOT NULL DEFAULT 0,
  runs INTEGER NOT NULL DEFAULT 0,
  refused INTEGER NOT NULL DEFAULT 0
);

-- Per provider per day: calls, errors, audio seconds and estimated cost. For the owner's eyes only.
CREATE TABLE try_provider_day (
  day TEXT NOT NULL,
  provider TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  audio_seconds REAL NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (day, provider)
);
