"""MLB Stats API からチーム情報・順位表・試合スケジュール・選手・リーダー・ボックススコアを取得。"""
from __future__ import annotations

import datetime as dt
import json
import sys
from typing import Any

import requests

from db import connect, init_db
from player_master import player_name_ja
from team_master import division_ja, league_ja, name_ja

API_BASE = "https://statsapi.mlb.com/api/v1"
JST = dt.timezone(dt.timedelta(hours=9))
SEASON = 2026


def _get(path: str, **params: Any) -> dict:
    url = f"{API_BASE}/{path}"
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_teams() -> None:
    data = _get("teams", sportId=1, season=SEASON, activeStatus="Y")
    rows = []
    for t in data["teams"]:
        team_id = t["id"]
        league_id = t.get("league", {}).get("id")
        division_id = t.get("division", {}).get("id")
        rows.append(
            (
                team_id,
                t["name"],
                name_ja(team_id, t["name"]),
                t.get("abbreviation"),
                league_id,
                league_ja(league_id) if league_id else None,
                division_id,
                t.get("division", {}).get("name"),
                division_ja(team_id, t.get("division", {}).get("name", "")),
            )
        )
    with connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO teams
            (team_id, name, name_ja, abbreviation, league_id, league_name_ja,
             division_id, division_name, division_name_ja)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
    print(f"[teams] upserted {len(rows)} rows")


def fetch_standings() -> None:
    data = _get(
        "standings",
        leagueId="103,104",
        season=SEASON,
        standingsTypes="regularSeason",
    )
    now = dt.datetime.now(JST).isoformat(timespec="seconds")
    rows = []
    for rec in data["records"]:
        for tr in rec["teamRecords"]:
            rd = None
            if "runDifferential" in tr:
                rd = tr["runDifferential"]
            records_split = tr.get("records", {}).get("splitRecords", [])
            last_ten = next(
                (f"{s['wins']}-{s['losses']}" for s in records_split if s.get("type") == "lastTen"),
                None,
            )
            streak = tr.get("streak", {}).get("streakCode")
            rows.append(
                (
                    tr["team"]["id"],
                    SEASON,
                    tr.get("wins"),
                    tr.get("losses"),
                    tr.get("winningPercentage"),
                    tr.get("gamesBack"),
                    int(tr.get("divisionRank") or 0) or None,
                    int(tr.get("leagueRank") or 0) or None,
                    streak,
                    last_ten,
                    rd,
                    now,
                )
            )
    with connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO standings
            (team_id, season, wins, losses, pct, games_back, division_rank,
             league_rank, streak, last_ten, run_diff, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
    print(f"[standings] upserted {len(rows)} rows")


def fetch_schedule(start: str, end: str) -> None:
    """start/end は YYYY-MM-DD。両端含む。"""
    data = _get(
        "schedule",
        sportId=1,
        startDate=start,
        endDate=end,
        hydrate="probablePitcher(note),decisions,linescore",
    )
    rows = []
    for date_block in data.get("dates", []):
        for g in date_block["games"]:
            teams = g["teams"]
            away, home = teams["away"], teams["home"]
            decisions = g.get("decisions", {}) or {}
            rows.append(
                (
                    g["gamePk"],
                    g.get("officialDate") or date_block.get("date"),
                    g.get("gameDate"),
                    g.get("status", {}).get("abstractGameState"),
                    g.get("status", {}).get("detailedState"),
                    away["team"]["id"],
                    away.get("score"),
                    home["team"]["id"],
                    home.get("score"),
                    g.get("venue", {}).get("name"),
                    g.get("seriesDescription"),
                    (away.get("probablePitcher") or {}).get("fullName"),
                    (home.get("probablePitcher") or {}).get("fullName"),
                    (decisions.get("winner") or {}).get("fullName"),
                    (decisions.get("loser") or {}).get("fullName"),
                    (decisions.get("save") or {}).get("fullName"),
                )
            )
    with connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO games
            (game_pk, game_date, game_datetime, status, detailed_state,
             away_team_id, away_score, home_team_id, home_score, venue,
             series_description, away_pitcher, home_pitcher,
             winning_pitcher, losing_pitcher, save_pitcher)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
    print(f"[schedule] upserted {len(rows)} games ({start} - {end})")


LEADER_CATEGORIES_HITTING = [
    "homeRuns", "battingAverage", "rbi", "hits",
    "stolenBases", "onBasePlusSlugging", "runs",
]
LEADER_CATEGORIES_PITCHING = [
    "earnedRunAverage", "wins", "strikeouts", "saves", "whip",
]


def fetch_rosters() -> None:
    with connect() as conn:
        team_ids = [r["team_id"] for r in conn.execute("SELECT team_id FROM teams")]

    players_rows: list[tuple] = []
    roster_rows: list[tuple] = []
    for tid in team_ids:
        try:
            data = _get(f"teams/{tid}/roster", rosterType="active", season=SEASON)
        except requests.HTTPError:
            continue
        for entry in data.get("roster", []):
            p = entry["person"]
            pos = entry.get("position", {}) or {}
            players_rows.append((p["id"], p["fullName"], player_name_ja(p["fullName"]), tid))
            roster_rows.append((
                tid, p["id"], SEASON,
                entry.get("jerseyNumber"),
                pos.get("code"),
                pos.get("abbreviation"),
                pos.get("name"),
                entry.get("status", {}).get("description"),
            ))

    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO players (player_id, full_name, full_name_ja, current_team_id) VALUES (?,?,?,?)",
            players_rows,
        )
        conn.executemany(
            """INSERT OR REPLACE INTO rosters
            (team_id, player_id, season, jersey_number, position_code,
             position_abbr, position_name, status)
            VALUES (?,?,?,?,?,?,?,?)""",
            roster_rows,
        )
        conn.commit()
    print(f"[rosters] {len(players_rows)} player entries / {len(roster_rows)} roster rows")


def _fetch_leader_category(category: str, league_id: int | None, stat_group: str, limit: int = 20) -> list[tuple]:
    params = {
        "leaderCategories": category,
        "statGroup": stat_group,            # hitting / pitching を必ず明示
        "season": SEASON,
        "sportId": 1,
        "limit": limit,
    }
    if league_id:
        params["leagueId"] = league_id
    data = _get("stats/leaders", **params)
    rows = []
    league_key = league_id or 0
    for cat in data.get("leagueLeaders", []):
        for ld in cat.get("leaders", []):
            full_name = ld.get("person", {}).get("fullName")
            rows.append((
                SEASON,
                league_key,
                category,
                int(ld.get("rank") or 0),
                ld.get("person", {}).get("id"),
                full_name,
                player_name_ja(full_name),
                ld.get("team", {}).get("id"),
                str(ld.get("value")),
            ))
    return rows


def fetch_leaders() -> None:
    all_rows: list[tuple] = []
    for cat in LEADER_CATEGORIES_HITTING:
        for lid in (103, 104, None):  # AL, NL, MLB全体
            try:
                all_rows.extend(_fetch_leader_category(cat, lid, "hitting", limit=15))
            except requests.HTTPError as e:
                print(f"  WARN leaders hitting/{cat}/{lid}: {e}", file=sys.stderr)
    for cat in LEADER_CATEGORIES_PITCHING:
        for lid in (103, 104, None):
            try:
                all_rows.extend(_fetch_leader_category(cat, lid, "pitching", limit=15))
            except requests.HTTPError as e:
                print(f"  WARN leaders pitching/{cat}/{lid}: {e}", file=sys.stderr)

    with connect() as conn:
        conn.execute("DELETE FROM leaders WHERE season=?", (SEASON,))
        conn.executemany(
            """INSERT OR REPLACE INTO leaders
            (season, league_id, category, rank, player_id, player_name, player_name_ja, team_id, value)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            all_rows,
        )
        conn.commit()
    print(f"[leaders] {len(all_rows)} rows")


def fetch_japanese_player_stats() -> None:
    """日本人選手 (full_name_ja IS NOT NULL) のシーズン成績を取得して保存。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT player_id, full_name FROM players WHERE full_name_ja IS NOT NULL"
        ).fetchall()
    now = dt.datetime.now(JST).isoformat(timespec="seconds")
    saved = 0
    for r in rows:
        pid = r["player_id"]
        for grp in ("hitting", "pitching"):
            try:
                data = _get(f"people/{pid}/stats",
                            stats="season", season=SEASON, group=grp)
            except requests.HTTPError as e:
                print(f"  WARN stats {pid}/{grp}: {e}", file=sys.stderr)
                continue
            stats_blocks = data.get("stats") or []
            if not stats_blocks:
                continue
            splits = stats_blocks[0].get("splits") or []
            if not splits:
                continue
            stat = splits[0].get("stat")
            if not stat:
                continue
            with connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO player_season_stats
                    (player_id, season, stat_group, stats_json, updated_at)
                    VALUES (?,?,?,?,?)""",
                    (pid, SEASON, grp, json.dumps(stat, ensure_ascii=False), now),
                )
                conn.commit()
            saved += 1
    print(f"[jp_stats] saved {saved} stat blocks for {len(rows)} players")


def fetch_boxscore_for_game(game_pk: int) -> None:
    """1試合のボックススコア + ラインスコアを取得して保存。"""
    try:
        box = _get(f"game/{game_pk}/boxscore")
        ls = _get(f"game/{game_pk}/linescore")
    except requests.HTTPError as e:
        print(f"  WARN boxscore {game_pk}: {e}", file=sys.stderr)
        return

    away = box["teams"]["away"]
    home = box["teams"]["home"]

    # team stats
    def stats_row(side: str, side_data: dict) -> tuple:
        bat = side_data.get("teamStats", {}).get("batting", {})
        pit = side_data.get("teamStats", {}).get("pitching", {})
        return (
            game_pk, side, side_data["team"]["id"],
            bat.get("avg"), bat.get("obp"), bat.get("slg"), bat.get("ops"),
            bat.get("runs"), bat.get("hits"), bat.get("homeRuns"), bat.get("rbi"),
            bat.get("baseOnBalls"), bat.get("strikeOuts"),
            pit.get("era"), pit.get("inningsPitched"),
            pit.get("strikeOuts"), pit.get("hits"), pit.get("runs"),
            pit.get("baseOnBalls"), pit.get("homeRuns"),
        )

    ls_teams = ls.get("teams", {}) or {}
    ls_away = ls_teams.get("away", {}) or {}
    ls_home = ls_teams.get("home", {}) or {}
    innings = ls.get("innings", []) or []
    line_row = (
        game_pk,
        ls_away.get("runs"), ls_away.get("hits"), ls_away.get("errors"),
        ls_home.get("runs"), ls_home.get("hits"), ls_home.get("errors"),
        json.dumps(innings, ensure_ascii=False),
    )

    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO boxscore_linescore
            (game_pk, away_runs, away_hits, away_errors,
             home_runs, home_hits, home_errors, innings_json)
            VALUES (?,?,?,?,?,?,?,?)""",
            line_row,
        )
        conn.executemany(
            """INSERT OR REPLACE INTO boxscore_team_stats
            (game_pk, side, team_id, bat_avg, bat_obp, bat_slg, bat_ops,
             bat_runs, bat_hits, bat_hr, bat_rbi, bat_bb, bat_so,
             pit_era, pit_innings, pit_strikeouts, pit_hits, pit_runs,
             pit_walks, pit_home_runs)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [stats_row("away", away), stats_row("home", home)],
        )
        conn.commit()


def fetch_recent_boxscores(days_back: int = 3) -> None:
    today = dt.datetime.now(JST).date()
    start = (today - dt.timedelta(days=days_back)).isoformat()
    with connect() as conn:
        rows = conn.execute(
            """SELECT game_pk FROM games
               WHERE status='Final' AND game_date >= ?""",
            (start,),
        ).fetchall()
    print(f"[boxscores] fetching {len(rows)} games...")
    for i, r in enumerate(rows, 1):
        fetch_boxscore_for_game(r["game_pk"])
        if i % 10 == 0:
            print(f"  ...{i}/{len(rows)}")
    print(f"[boxscores] done ({len(rows)} games)")


def main() -> None:
    init_db()
    today = dt.datetime.now(JST).date()
    # 直近7日 + 今後3日
    start = (today - dt.timedelta(days=7)).isoformat()
    end = (today + dt.timedelta(days=3)).isoformat()

    fetch_teams()
    fetch_standings()
    fetch_schedule(start, end)
    fetch_rosters()
    fetch_leaders()
    fetch_japanese_player_stats()
    fetch_recent_boxscores(days_back=3)


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        sys.exit(1)
