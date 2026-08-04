"""SQLite schema and connection helper."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "mlb.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    team_id        INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    name_ja        TEXT,
    abbreviation   TEXT,
    league_id      INTEGER,
    league_name_ja TEXT,
    division_id    INTEGER,
    division_name  TEXT,
    division_name_ja TEXT
);

CREATE TABLE IF NOT EXISTS standings (
    team_id        INTEGER PRIMARY KEY,
    season         INTEGER,
    wins           INTEGER,
    losses         INTEGER,
    pct            TEXT,
    games_back     TEXT,
    division_rank  INTEGER,
    league_rank    INTEGER,
    streak         TEXT,
    last_ten       TEXT,
    run_diff       INTEGER,
    updated_at     TEXT,
    FOREIGN KEY(team_id) REFERENCES teams(team_id)
);

CREATE TABLE IF NOT EXISTS games (
    game_pk            INTEGER PRIMARY KEY,
    game_date          TEXT,
    game_datetime      TEXT,
    status             TEXT,
    detailed_state     TEXT,
    away_team_id       INTEGER,
    away_score         INTEGER,
    home_team_id       INTEGER,
    home_score         INTEGER,
    venue              TEXT,
    series_description TEXT,
    away_pitcher       TEXT,
    away_pitcher_id    INTEGER,
    home_pitcher       TEXT,
    home_pitcher_id    INTEGER,
    winning_pitcher    TEXT,
    losing_pitcher     TEXT,
    save_pitcher       TEXT
);

CREATE TABLE IF NOT EXISTS team_season_stats (
    team_id        INTEGER,
    season         INTEGER,
    stat_group     TEXT,            -- 'hitting' or 'pitching'
    stats_json     TEXT,
    updated_at     TEXT,
    PRIMARY KEY (team_id, season, stat_group),
    FOREIGN KEY (team_id) REFERENCES teams(team_id)
);

CREATE TABLE IF NOT EXISTS game_predictions (
    game_pk        INTEGER PRIMARY KEY,
    predicted_at   TEXT,
    home_prob      INTEGER,
    away_prob      INTEGER
);

-- 選手のトレード (トレード期限前後のロースター変動を把握するため)
CREATE TABLE IF NOT EXISTS player_trades (
    player_id      INTEGER,
    trade_date     TEXT,
    player_name    TEXT,
    from_team_id   INTEGER,
    to_team_id     INTEGER,
    role           TEXT,      -- 'P'=投手 / 'B'=野手
    summary        TEXT,      -- 移籍時点の今季成績(表示用)
    impact         REAL,      -- 主力度(投手=投球回, 野手=打席/10)。並べ替え用
    PRIMARY KEY (player_id, trade_date)
);

-- 第一イニング予想の事前スナップショット (試合前に凍結し、後で1回の実績と照合)
CREATE TABLE IF NOT EXISTS first_inning_predictions (
    game_pk        INTEGER PRIMARY KEY,
    predicted_at   TEXT,
    away_ahead     INTEGER,     -- 1回終了時ビジターリードの確率(%)
    tie            INTEGER,     -- 五分(0-0含む)の確率(%)
    home_ahead     INTEGER,     -- ホームリードの確率(%)
    backfill       INTEGER DEFAULT 0   -- 1=事後推定(近似), 0=真の事前スナップショット
);

-- チームの状況別成績 (standings splitRecords / expectedRecords)
CREATE TABLE IF NOT EXISTS team_splits (
    team_id        INTEGER,
    season         INTEGER,
    split_type     TEXT,            -- 'home','away','day','night','oneRun','extraInning','xWinLoss' など
    wins           INTEGER,
    losses         INTEGER,
    pct            TEXT,
    updated_at     TEXT,
    PRIMARY KEY (team_id, season, split_type)
);

-- チームの先制時/被先制時の成績 (linescore集計)
CREATE TABLE IF NOT EXISTS team_first_score (
    team_id        INTEGER,
    season         INTEGER,
    scored_first_w INTEGER,         -- 先制した試合の勝利数
    scored_first_l INTEGER,
    allowed_first_w INTEGER,        -- 先制された試合の勝利数
    allowed_first_l INTEGER,
    games          INTEGER,         -- 集計対象試合数
    first_inn_scored  INTEGER,      -- 1回に得点した試合数
    first_inn_allowed INTEGER,      -- 1回に失点した試合数
    first_inn_runs_scored  INTEGER, -- 1回に取った総得点
    first_inn_runs_allowed INTEGER, -- 1回に取られた総失点
    updated_at     TEXT,
    PRIMARY KEY (team_id, season)
);

CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date);
CREATE INDEX IF NOT EXISTS idx_games_teams ON games(away_team_id, home_team_id);

CREATE TABLE IF NOT EXISTS players (
    player_id      INTEGER PRIMARY KEY,
    full_name      TEXT,
    full_name_ja   TEXT,
    current_team_id INTEGER
);

CREATE TABLE IF NOT EXISTS rosters (
    team_id        INTEGER,
    player_id      INTEGER,
    season         INTEGER,
    jersey_number  TEXT,
    position_code  TEXT,
    position_abbr  TEXT,
    position_name  TEXT,
    status         TEXT,
    PRIMARY KEY(team_id, player_id, season),
    FOREIGN KEY(team_id) REFERENCES teams(team_id),
    FOREIGN KEY(player_id) REFERENCES players(player_id)
);

CREATE TABLE IF NOT EXISTS leaders (
    season         INTEGER,
    league_id      INTEGER,   -- 103=AL, 104=NL, 0=MLB
    category       TEXT,
    rank           INTEGER,
    player_id      INTEGER,
    player_name    TEXT,
    player_name_ja TEXT,
    team_id        INTEGER,
    value          TEXT,
    PRIMARY KEY(season, league_id, category, rank)
);

CREATE TABLE IF NOT EXISTS boxscore_linescore (
    game_pk        INTEGER PRIMARY KEY,
    away_runs      INTEGER,
    away_hits      INTEGER,
    away_errors    INTEGER,
    home_runs      INTEGER,
    home_hits      INTEGER,
    home_errors    INTEGER,
    innings_json   TEXT,
    FOREIGN KEY(game_pk) REFERENCES games(game_pk)
);

CREATE TABLE IF NOT EXISTS boxscore_team_stats (
    game_pk        INTEGER,
    side           TEXT,           -- 'away' or 'home'
    team_id        INTEGER,
    bat_avg        TEXT,
    bat_obp        TEXT,
    bat_slg        TEXT,
    bat_ops        TEXT,
    bat_runs       INTEGER,
    bat_hits       INTEGER,
    bat_hr         INTEGER,
    bat_rbi        INTEGER,
    bat_bb         INTEGER,
    bat_so         INTEGER,
    pit_era        TEXT,
    pit_innings    TEXT,
    pit_strikeouts INTEGER,
    pit_hits       INTEGER,
    pit_runs       INTEGER,
    pit_walks      INTEGER,
    pit_home_runs  INTEGER,
    PRIMARY KEY(game_pk, side),
    FOREIGN KEY(game_pk) REFERENCES games(game_pk)
);

CREATE INDEX IF NOT EXISTS idx_leaders_lookup ON leaders(season, league_id, category);
CREATE INDEX IF NOT EXISTS idx_rosters_team ON rosters(team_id, season);

CREATE TABLE IF NOT EXISTS player_season_stats (
    player_id      INTEGER,
    season         INTEGER,
    stat_group     TEXT,           -- 'hitting' or 'pitching'
    stats_json     TEXT,
    updated_at     TEXT,
    PRIMARY KEY (player_id, season, stat_group),
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);

CREATE TABLE IF NOT EXISTS player_game_log (
    player_id      INTEGER,
    game_pk        INTEGER,
    season         INTEGER,
    stat_group     TEXT,            -- 'pitching' / 'hitting'
    game_date      TEXT,
    team_id        INTEGER,
    opponent_id    INTEGER,
    is_home        INTEGER,         -- 0/1
    is_win         INTEGER,         -- 0/1/-1(N/A)
    stats_json     TEXT,
    PRIMARY KEY (player_id, game_pk, stat_group)
    -- games テーブルに無い試合（オープン戦・スプリングトレーニング等）も
    -- 受け入れるためFK制約は付けない
);

CREATE TABLE IF NOT EXISTS player_split_stats (
    player_id      INTEGER,
    season         INTEGER,
    stat_group     TEXT,            -- 'pitching' / 'hitting'
    split_type     TEXT,            -- 'byMonth', 'homeAndAway', 'vsHand'
    split_key      TEXT,            -- 月番号 / 'home'or'away' / 'vsLeft'or'vsRight'
    stats_json     TEXT,
    PRIMARY KEY (player_id, season, stat_group, split_type, split_key)
);

CREATE INDEX IF NOT EXISTS idx_game_log_player ON player_game_log(player_id, season);
CREATE INDEX IF NOT EXISTS idx_split_player ON player_split_stats(player_id, season, stat_group);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """既存テーブルに列が無ければ ALTER で追加（冪等）。"""
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # 旧バージョンのDBに対する後方互換マイグレーション
        _ensure_column(conn, "players", "full_name_ja", "TEXT")
        _ensure_column(conn, "leaders", "player_name_ja", "TEXT")
        _ensure_column(conn, "games", "away_pitcher_id", "INTEGER")
        _ensure_column(conn, "games", "home_pitcher_id", "INTEGER")
        _ensure_column(conn, "team_first_score", "games", "INTEGER")
        _ensure_column(conn, "team_first_score", "first_inn_scored", "INTEGER")
        _ensure_column(conn, "team_first_score", "first_inn_allowed", "INTEGER")
        _ensure_column(conn, "team_first_score", "first_inn_runs_scored", "INTEGER")
        _ensure_column(conn, "team_first_score", "first_inn_runs_allowed", "INTEGER")
        conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"Initialized DB at {DB_PATH}")
