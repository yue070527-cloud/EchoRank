PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS albums (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    cover_url TEXT,
    cover_color TEXT NOT NULL DEFAULT '#777777'
        CHECK (cover_color GLOB '#[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]')
);

CREATE TABLE IF NOT EXISTS songs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    album_id TEXT NOT NULL REFERENCES albums(id)
);

CREATE TABLE IF NOT EXISTS artists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS song_artists (
    song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    artist_id TEXT NOT NULL REFERENCES artists(id),
    credit_order INTEGER NOT NULL CHECK (credit_order >= 0),
    PRIMARY KEY (song_id, artist_id),
    UNIQUE (song_id, credit_order)
);

CREATE TABLE IF NOT EXISTS chart_periods (
    id INTEGER PRIMARY KEY,
    period_type TEXT NOT NULL CHECK (period_type IN ('daily', 'weekly')),
    period_key TEXT NOT NULL,
    target_date TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    collected_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('collecting', 'settled', 'partial', 'missing', 'failed')),
    coverage REAL NOT NULL DEFAULT 0 CHECK (coverage >= 0 AND coverage <= 1),
    input_fingerprint TEXT,
    source_snapshot TEXT,
    frozen INTEGER NOT NULL DEFAULT 0 CHECK (frozen IN (0, 1)),
    UNIQUE (period_type, period_key)
);

CREATE TABLE IF NOT EXISTS netease_snapshot_entries (
    period_id INTEGER NOT NULL REFERENCES chart_periods(id) ON DELETE CASCADE,
    song_id TEXT NOT NULL REFERENCES songs(id),
    source_rank INTEGER NOT NULL CHECK (source_rank BETWEEN 1 AND 100),
    weekly_play_count INTEGER NOT NULL CHECK (weekly_play_count >= 0),
    PRIMARY KEY (period_id, song_id),
    UNIQUE (period_id, source_rank)
);

CREATE TABLE IF NOT EXISTS point_ledger (
    id INTEGER PRIMARY KEY,
    period_id INTEGER NOT NULL REFERENCES chart_periods(id) ON DELETE CASCADE,
    song_id TEXT NOT NULL REFERENCES songs(id),
    source TEXT NOT NULL CHECK (source IN ('netease', 'physical', 'bilibili', 'other', 'legacyBonus', 'manualAdjustment')),
    points REAL NOT NULL,
    scoring_version TEXT NOT NULL,
    external_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (source, external_key)
);

CREATE INDEX IF NOT EXISTS point_ledger_period_song
ON point_ledger(period_id, song_id);

CREATE TABLE IF NOT EXISTS chart_entries (
    period_id INTEGER NOT NULL REFERENCES chart_periods(id) ON DELETE CASCADE,
    song_id TEXT NOT NULL REFERENCES songs(id),
    rank INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 100),
    previous_rank INTEGER CHECK (previous_rank BETWEEN 1 AND 100),
    movement_type TEXT NOT NULL CHECK (movement_type IN ('up', 'down', 'same', 'new', 're')),
    movement_value INTEGER NOT NULL CHECK (movement_value >= 0),
    peak INTEGER NOT NULL CHECK (peak BETWEEN 1 AND 100),
    periods INTEGER NOT NULL CHECK (periods >= 1),
    netease_points REAL NOT NULL,
    physical_points REAL NOT NULL,
    bilibili_points REAL NOT NULL,
    other_points REAL NOT NULL,
    legacy_bonus REAL NOT NULL,
    manual_adjustment REAL NOT NULL,
    total_points REAL NOT NULL,
    PRIMARY KEY (period_id, song_id),
    UNIQUE (period_id, rank)
);
