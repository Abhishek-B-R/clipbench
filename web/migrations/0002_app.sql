-- clipbench app tables. Timestamps are unix epoch milliseconds (INTEGER).
-- user ids reference the Better Auth "user" table; rows go away with the user.

CREATE TABLE watchlist (
  user_id TEXT NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
  provider_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, provider_id)
);

CREATE TABLE alert_prefs (
  user_id TEXT PRIMARY KEY REFERENCES "user"(id) ON DELETE CASCADE,
  email_on_new_run INTEGER NOT NULL DEFAULT 0,
  email_on_rank_change INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);

CREATE TABLE comparisons (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  providers TEXT NOT NULL,          -- JSON array of provider ids
  tier TEXT NOT NULL,
  metric TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '[]',  -- JSON array of clip tags
  public INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX comparisons_user ON comparisons (user_id, created_at);

CREATE TABLE requests (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES "user"(id) ON DELETE SET NULL,
  kind TEXT NOT NULL CHECK (kind IN ('provider', 'language')),
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'planned', 'done', 'declined')),
  created_at INTEGER NOT NULL
);

CREATE TABLE votes (
  request_id TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (request_id, user_id)
);
CREATE INDEX votes_request ON votes (request_id);

CREATE TABLE api_keys (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  prefix TEXT NOT NULL,             -- first characters of the secret, shown in the UI
  hash TEXT NOT NULL UNIQUE,        -- hex SHA-256 of the full secret; the secret itself is never stored
  created_at INTEGER NOT NULL,
  last_used_at INTEGER
);
CREATE INDEX api_keys_user ON api_keys (user_id);

-- Fixed window counters: fallback limiter when the Workers rate limiting binding is not bound.
CREATE TABLE rate_limits (
  key TEXT NOT NULL,
  window_start INTEGER NOT NULL,    -- epoch seconds, start of the window
  count INTEGER NOT NULL,
  PRIMARY KEY (key, window_start)
);

-- One row per run whose "new run" email went out, so a retry never emails twice.
CREATE TABLE run_notifications (
  run_id TEXT PRIMARY KEY,
  sent_at INTEGER NOT NULL,
  recipients INTEGER NOT NULL
);
