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
    entity_type TEXT NOT NULL CHECK (entity_type IN ('songs', 'albums', 'artists')),
    period_type TEXT NOT NULL CHECK (period_type IN ('daily', 'weekly', 'monthly', 'yearly')),
    period_key TEXT NOT NULL,
    target_date TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    collected_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('collecting', 'settled', 'partial', 'missing', 'failed')),
    coverage REAL NOT NULL DEFAULT 0 CHECK (coverage >= 0 AND coverage <= 1),
    input_fingerprint TEXT,
    source_snapshot TEXT,
    frozen INTEGER NOT NULL DEFAULT 0 CHECK (frozen IN (0, 1)),
    UNIQUE (entity_type, period_type, period_key)
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

CREATE TABLE IF NOT EXISTS bilibili_view_events (
    id INTEGER PRIMARY KEY,
    period_id INTEGER NOT NULL REFERENCES chart_periods(id),
    song_id TEXT NOT NULL REFERENCES songs(id),
    view_count INTEGER NOT NULL CHECK (view_count >= 0),
    video_ref TEXT,
    notes TEXT,
    scoring_version TEXT NOT NULL,
    external_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE (period_id, song_id)
);

CREATE TABLE IF NOT EXISTS manual_adjustment_events (
    id INTEGER PRIMARY KEY,
    period_id INTEGER NOT NULL REFERENCES chart_periods(id),
    song_id TEXT NOT NULL REFERENCES songs(id),
    points REAL NOT NULL CHECK (points <> 0),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    external_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS physical_events (
    id INTEGER PRIMARY KEY,
    album_id TEXT NOT NULL REFERENCES albums(id),
    purchase_date TEXT NOT NULL,
    edition_label TEXT NOT NULL,
    format TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 1),
    edition_weight REAL NOT NULL CHECK (edition_weight > 0),
    purchase_weight REAL NOT NULL CHECK (purchase_weight > 0),
    duration_days INTEGER NOT NULL DEFAULT 28 CHECK (duration_days = 28),
    rank_schedule_version TEXT NOT NULL,
    scoring_version TEXT NOT NULL,
    external_key TEXT NOT NULL UNIQUE,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS physical_event_tracks (
    event_id INTEGER NOT NULL REFERENCES physical_events(id) ON DELETE CASCADE,
    song_id TEXT NOT NULL REFERENCES songs(id),
    track_weight REAL NOT NULL DEFAULT 1 CHECK (track_weight > 0),
    PRIMARY KEY (event_id, song_id)
);

CREATE TABLE IF NOT EXISTS physical_event_artists (
    event_id INTEGER NOT NULL REFERENCES physical_events(id) ON DELETE CASCADE,
    artist_id TEXT NOT NULL REFERENCES artists(id),
    share REAL NOT NULL CHECK (share > 0 AND share <= 1),
    PRIMARY KEY (event_id, artist_id)
);

CREATE TABLE IF NOT EXISTS physical_reference_points (
    event_id INTEGER NOT NULL REFERENCES physical_events(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 100),
    points REAL NOT NULL CHECK (points >= 0),
    PRIMARY KEY (event_id, rank)
);

CREATE TABLE IF NOT EXISTS physical_releases (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES physical_events(id),
    period_id INTEGER NOT NULL REFERENCES chart_periods(id),
    day_index INTEGER NOT NULL CHECK (day_index BETWEEN 1 AND 28),
    reference_rank INTEGER NOT NULL CHECK (reference_rank BETWEEN 1 AND 100),
    reference_points REAL NOT NULL CHECK (reference_points >= 0),
    points REAL NOT NULL CHECK (points >= 0),
    scoring_version TEXT NOT NULL,
    rank_schedule_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (event_id, period_id)
);

CREATE TRIGGER IF NOT EXISTS protect_frozen_ledger_update
BEFORE UPDATE ON point_ledger
WHEN EXISTS (SELECT 1 FROM chart_periods WHERE id=OLD.period_id AND frozen=1)
BEGIN
    SELECT RAISE(ABORT, '已结算周期账本不可修改');
END;

CREATE TRIGGER IF NOT EXISTS protect_frozen_ledger_delete
BEFORE DELETE ON point_ledger
WHEN EXISTS (SELECT 1 FROM chart_periods WHERE id=OLD.period_id AND frozen=1)
BEGIN
    SELECT RAISE(ABORT, '已结算周期账本不可删除');
END;

CREATE TRIGGER IF NOT EXISTS protect_frozen_bilibili_update
BEFORE UPDATE ON bilibili_view_events
WHEN EXISTS (SELECT 1 FROM chart_periods WHERE id=OLD.period_id AND frozen=1)
BEGIN
    SELECT RAISE(ABORT, '已结算周期B站事件不可修改');
END;

CREATE TRIGGER IF NOT EXISTS protect_frozen_bilibili_delete
BEFORE DELETE ON bilibili_view_events
WHEN EXISTS (SELECT 1 FROM chart_periods WHERE id=OLD.period_id AND frozen=1)
BEGIN
    SELECT RAISE(ABORT, '已结算周期B站事件不可删除');
END;

CREATE TRIGGER IF NOT EXISTS protect_frozen_release_update
BEFORE UPDATE ON physical_releases
WHEN EXISTS (SELECT 1 FROM chart_periods WHERE id=OLD.period_id AND frozen=1)
BEGIN
    SELECT RAISE(ABORT, '已结算周期实体释放不可修改');
END;

CREATE TRIGGER IF NOT EXISTS protect_frozen_release_delete
BEFORE DELETE ON physical_releases
WHEN EXISTS (SELECT 1 FROM chart_periods WHERE id=OLD.period_id AND frozen=1)
BEGIN
    SELECT RAISE(ABORT, '已结算周期实体释放不可删除');
END;

CREATE TRIGGER IF NOT EXISTS protect_physical_event_update
BEFORE UPDATE ON physical_events
BEGIN SELECT RAISE(ABORT, '实体事件不可修改'); END;

CREATE TRIGGER IF NOT EXISTS protect_physical_event_delete
BEFORE DELETE ON physical_events
BEGIN SELECT RAISE(ABORT, '实体事件不可删除'); END;

CREATE TRIGGER IF NOT EXISTS protect_physical_track_update
BEFORE UPDATE ON physical_event_tracks
BEGIN SELECT RAISE(ABORT, '实体事件曲目不可修改'); END;

CREATE TRIGGER IF NOT EXISTS protect_physical_track_delete
BEFORE DELETE ON physical_event_tracks
BEGIN SELECT RAISE(ABORT, '实体事件曲目不可删除'); END;

CREATE TRIGGER IF NOT EXISTS protect_physical_artist_update
BEFORE UPDATE ON physical_event_artists
BEGIN SELECT RAISE(ABORT, '实体事件艺人不可修改'); END;

CREATE TRIGGER IF NOT EXISTS protect_physical_artist_delete
BEFORE DELETE ON physical_event_artists
BEGIN SELECT RAISE(ABORT, '实体事件艺人不可删除'); END;

CREATE TRIGGER IF NOT EXISTS protect_reference_update
BEFORE UPDATE ON physical_reference_points
BEGIN SELECT RAISE(ABORT, '实体参考曲线不可修改'); END;

CREATE TRIGGER IF NOT EXISTS protect_reference_delete
BEFORE DELETE ON physical_reference_points
BEGIN SELECT RAISE(ABORT, '实体参考曲线不可删除'); END;

CREATE TRIGGER IF NOT EXISTS protect_manual_adjustment_update
BEFORE UPDATE ON manual_adjustment_events
BEGIN SELECT RAISE(ABORT, '人工修正事件不可修改'); END;

CREATE TRIGGER IF NOT EXISTS protect_manual_adjustment_delete
BEFORE DELETE ON manual_adjustment_events
BEGIN SELECT RAISE(ABORT, '人工修正事件不可删除'); END;

CREATE TABLE IF NOT EXISTS chart_entries (
    period_id INTEGER NOT NULL REFERENCES chart_periods(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL,
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
    PRIMARY KEY (period_id, entity_id),
    UNIQUE (period_id, rank)
);
