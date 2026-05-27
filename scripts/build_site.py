"""SQLite から HTML を生成する。出力先は site/ ."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from db import connect
from venue_master import venue_short

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"

JST = dt.timezone(dt.timedelta(hours=9))
SEASON = 2026

DIVISION_ORDER = [
    (103, "アメリカン・リーグ", [
        (201, "ア・リーグ東地区"),
        (202, "ア・リーグ中地区"),
        (200, "ア・リーグ西地区"),
    ]),
    (104, "ナショナル・リーグ", [
        (204, "ナ・リーグ東地区"),
        (205, "ナ・リーグ中地区"),
        (203, "ナ・リーグ西地区"),
    ]),
]

CATEGORY_LABEL = {
    "battingAverage":      "打率",
    "homeRuns":            "本塁打",
    "rbi":                 "打点",
    "hits":                "安打",
    "onBasePlusSlugging":  "OPS",
    "stolenBases":         "盗塁",
    "runs":                "得点",
    "earnedRunAverage":    "防御率",
    "wins":                "勝利",
    "strikeouts":          "奪三振",
    "saves":               "セーブ",
    "whip":                "WHIP",
}
SCOPE_LABEL = {0: "MLB", 103: "AL", 104: "NL"}


LEADER_HITTING = [
    ("battingAverage", "打率", "AVG"),
    ("homeRuns", "本塁打", "HR"),
    ("rbi", "打点", "RBI"),
    ("hits", "安打", "H"),
    ("onBasePlusSlugging", "OPS", "OPS"),
    ("stolenBases", "盗塁", "SB"),
    ("runs", "得点", "R"),
]
LEADER_PITCHING = [
    ("earnedRunAverage", "防御率", "ERA"),
    ("wins", "勝利", "W"),
    ("strikeouts", "奪三振", "K"),
    ("saves", "セーブ", "S"),
    ("whip", "WHIP", "WHIP"),
]

POSITION_GROUP_ORDER = [
    ("投手", {"P"}),
    ("捕手", {"C"}),
    ("内野手", {"1B", "2B", "3B", "SS", "IF"}),
    ("外野手", {"LF", "CF", "RF", "OF"}),
    ("指名打者", {"DH"}),
    ("その他", set()),
]


# -------- env --------
def get_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["venue_short"] = venue_short
    return env


# -------- loaders --------
def load_teams() -> dict[int, dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM teams").fetchall()
    return {r["team_id"]: dict(r) for r in rows}


def load_standings_map() -> dict[int, dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM standings").fetchall()
    return {r["team_id"]: dict(r) for r in rows}


def load_standings_by_division() -> dict[int, list[dict]]:
    sql = """
        SELECT s.*, t.name_ja, t.name, t.abbreviation, t.division_id
        FROM standings s JOIN teams t ON s.team_id = t.team_id
        ORDER BY t.division_id, s.division_rank
    """
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql)]
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["division_id"]].append(r)
    return out


def load_games(date_iso: str) -> list[dict]:
    sql = """
        SELECT g.*,
               ta.name_ja AS away_name_ja, ta.abbreviation AS away_abbr,
               th.name_ja AS home_name_ja, th.abbreviation AS home_abbr
        FROM games g
        LEFT JOIN teams ta ON g.away_team_id = ta.team_id
        LEFT JOIN teams th ON g.home_team_id = th.team_id
        WHERE g.game_date = ?
        ORDER BY g.game_datetime
    """
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, (date_iso,))]


def load_games_for_team(team_id: int, today_iso: str) -> tuple[list[dict], list[dict]]:
    """直近Final試合(降順) と 今後の試合(昇順) を返す。"""
    sql = """
        SELECT g.*,
               ta.name_ja AS away_name_ja, ta.abbreviation AS away_abbr,
               th.name_ja AS home_name_ja, th.abbreviation AS home_abbr
        FROM games g
        LEFT JOIN teams ta ON g.away_team_id = ta.team_id
        LEFT JOIN teams th ON g.home_team_id = th.team_id
        WHERE g.away_team_id = ? OR g.home_team_id = ?
        ORDER BY g.game_datetime
    """
    with connect() as conn:
        all_rows = [dict(r) for r in conn.execute(sql, (team_id, team_id))]

    finished = [g for g in all_rows if g["status"] == "Final"]
    upcoming = [g for g in all_rows if g["status"] != "Final"]

    # 直近Final 試合は新しい順、最大10
    finished_recent = list(reversed(finished))[:10]
    upcoming = upcoming[:10]

    def annotate(g: dict) -> dict:
        is_home = g["home_team_id"] == team_id
        g["is_home"] = is_home
        g["opponent_name_ja"] = g["away_name_ja"] if is_home else g["home_name_ja"]
        g["opponent_abbr"] = g["away_abbr"] if is_home else g["home_abbr"]
        our_score = g["home_score"] if is_home else g["away_score"]
        opp_score = g["away_score"] if is_home else g["home_score"]
        g["our_score"] = our_score
        g["opp_score"] = opp_score
        if g["status"] == "Final" and our_score is not None and opp_score is not None:
            g["result"] = "勝" if our_score > opp_score else ("負" if our_score < opp_score else "分")
        else:
            g["result"] = ""
        g["our_pitcher"] = g["home_pitcher"] if is_home else g["away_pitcher"]
        g["opp_pitcher"] = g["away_pitcher"] if is_home else g["home_pitcher"]
        g["start_jst"] = to_jst_time(g["game_datetime"])
        return g

    return [annotate(g) for g in finished_recent], [annotate(g) for g in upcoming]


def load_roster(team_id: int) -> list[dict]:
    sql = """
        SELECT r.jersey_number, r.position_abbr, r.position_name, r.status,
               p.player_id, p.full_name, p.full_name_ja,
               EXISTS(
                 SELECT 1 FROM player_game_log gl
                 WHERE gl.player_id = p.player_id AND gl.season = ?
                       AND gl.stat_group = 'pitching'
               ) AS is_starter
        FROM rosters r JOIN players p ON r.player_id = p.player_id
        WHERE r.team_id = ? AND r.season = ?
        ORDER BY r.position_abbr, p.full_name
    """
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, (SEASON, team_id, SEASON))]


def group_roster(roster: list[dict]) -> list[tuple[str, list[dict]]]:
    result: list[tuple[str, list[dict]]] = []
    used: set[int] = set()
    for name, abbrs in POSITION_GROUP_ORDER[:-1]:
        members = [p for i, p in enumerate(roster) if p["position_abbr"] in abbrs and i not in used]
        for p in members:
            used.add(roster.index(p))
        if members:
            result.append((name, members))
    others = [p for i, p in enumerate(roster) if i not in used]
    if others:
        result.append(("その他", others))
    return result


def load_leaders(scope_league_id: int) -> dict[str, list[dict]]:
    sql = """
        SELECT l.*, t.abbreviation AS team_abbr,
               EXISTS(
                 SELECT 1 FROM player_game_log gl
                 WHERE gl.player_id = l.player_id AND gl.season = l.season
                       AND gl.stat_group = 'pitching'
               ) AS has_pitcher_page
        FROM leaders l LEFT JOIN teams t ON l.team_id = t.team_id
        WHERE l.season = ? AND l.league_id = ?
        ORDER BY l.category, l.rank
    """
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, (SEASON, scope_league_id))]
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["category"]].append(r)
    return out


def load_boxscore(game_pk: int) -> tuple[dict | None, list[dict], list[dict]]:
    with connect() as conn:
        ls = conn.execute(
            "SELECT * FROM boxscore_linescore WHERE game_pk=?", (game_pk,)
        ).fetchone()
        ts_rows = conn.execute(
            """SELECT ts.*, t.name_ja AS team_name_ja
               FROM boxscore_team_stats ts
               LEFT JOIN teams t ON ts.team_id = t.team_id
               WHERE ts.game_pk=? ORDER BY ts.side DESC""",
            (game_pk,),
        ).fetchall()
    ls_dict = dict(ls) if ls else None
    innings_list: list[dict] = []
    if ls_dict and ls_dict.get("innings_json"):
        try:
            raw = json.loads(ls_dict["innings_json"])
            for i in raw:
                innings_list.append({
                    "num": i.get("num"),
                    "away": i.get("away", {}).get("runs"),
                    "home": i.get("home", {}).get("runs"),
                })
        except json.JSONDecodeError:
            pass
    return ls_dict, innings_list, [dict(r) for r in ts_rows]


# -------- helpers --------
def to_jst_time(iso_dt: str | None) -> str:
    if not iso_dt:
        return ""
    try:
        d = dt.datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
        return d.astimezone(JST).strftime("%H:%M")
    except Exception:
        return ""


def to_jst_date(iso_dt: str | None) -> str:
    if not iso_dt:
        return ""
    try:
        d = dt.datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
        return d.astimezone(JST).date().isoformat()
    except Exception:
        return ""


def build_leagues_view(stand_by_div: dict[int, list[dict]]):
    leagues = []
    flat_divs = []
    for league_id, league_ja, divs in DIVISION_ORDER:
        league_divs = []
        for div_id, div_name in divs:
            teams = stand_by_div.get(div_id, [])
            block = {"id": div_id, "name_ja": div_name, "teams": teams}
            league_divs.append(block)
            flat_divs.append(block)
        leagues.append({"id": league_id, "name_ja": league_ja, "divisions": league_divs})
    return leagues, flat_divs


def render(env: Environment, name: str, context: dict, out: Path) -> None:
    tmpl = env.get_template(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(tmpl.render(**context), encoding="utf-8")


def copy_static() -> None:
    dst = SITE / "static"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(STATIC, dst)


# -------- builders --------
def build_index(env, base_ctx, today, leagues, flat_divs) -> None:
    today_iso = today.isoformat()
    today_games_raw = (
        load_games(today_iso)
        + load_games((today - dt.timedelta(days=1)).isoformat())
    )
    today_games = []
    for g in today_games_raw:
        g["start_jst"] = to_jst_time(g["game_datetime"])
        if to_jst_date(g["game_datetime"]) == today_iso:
            today_games.append(g)

    recent_games = []
    for i in range(1, 4):
        d = today - dt.timedelta(days=i)
        d_iso = d.isoformat()
        games = [g for g in load_games(d_iso) if g["status"] == "Final"]
        if not games:
            continue
        recent_games.append({
            "date": d_iso,
            "label": d.strftime("%m月%d日 (米国時間)"),
            "games": games,
        })

    render(env, "index.html", {
        **base_ctx,
        "active": "top",
        "today_label": today.strftime("%Y年%m月%d日 (JST)"),
        "today_games": today_games,
        "divisions": flat_divs,
        "recent_games": recent_games,
    }, SITE / "index.html")


def build_standings(env, base_ctx, leagues) -> None:
    render(env, "standings.html", {**base_ctx, "active": "standings", "leagues": leagues}, SITE / "standings.html")


def build_teams_index(env, base_ctx, leagues) -> None:
    render(env, "teams_index.html", {**base_ctx, "active": "teams", "leagues": leagues}, SITE / "teams.html")


def build_team_pages(env, base_ctx, teams: dict, standings: dict, today) -> None:
    today_iso = today.isoformat()
    for team_id, team in teams.items():
        recent, upcoming = load_games_for_team(team_id, today_iso)
        roster = load_roster(team_id)
        ctx = {
            **base_ctx,
            "active": "teams",
            "root": "../",
            "team": team,
            "standing": standings.get(team_id, {}),
            "recent_games": recent,
            "upcoming_games": upcoming,
            "roster": roster,
            "roster_grouped": group_roster(roster),
        }
        out = SITE / "teams" / f"{team['abbreviation'].lower()}.html"
        render(env, "team.html", ctx, out)


def build_game_pages(env, base_ctx, teams: dict) -> None:
    # ボックススコアが入っている試合のみ生成
    with connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT g.game_pk FROM games g
               JOIN boxscore_linescore b ON g.game_pk = b.game_pk"""
        ).fetchall()
    game_pks = [r["game_pk"] for r in rows]

    for game_pk in game_pks:
        with connect() as conn:
            g = conn.execute("SELECT * FROM games WHERE game_pk=?", (game_pk,)).fetchone()
        if not g:
            continue
        game = dict(g)
        away = teams.get(game["away_team_id"])
        home = teams.get(game["home_team_id"])
        if not (away and home):
            continue
        ls, innings, team_stats = load_boxscore(game_pk)
        ctx = {
            **base_ctx,
            "active": None,
            "root": "../",
            "game": game,
            "away": away,
            "home": home,
            "linescore": ls,
            "innings": innings,
            "team_stats": team_stats,
        }
        out = SITE / "games" / f"{game_pk}.html"
        render(env, "game.html", ctx, out)


def load_japanese_player_stats() -> tuple[list[dict], list[dict], list[dict]]:
    """日本人選手の (打者リスト, 投手リスト, リーダーランク一覧) を返す。"""
    with connect() as conn:
        stat_rows = conn.execute(
            """SELECT p.player_id, p.full_name, p.full_name_ja,
                      ps.stat_group, ps.stats_json,
                      t.name_ja AS team_ja, t.abbreviation AS team_abbr
               FROM player_season_stats ps
               JOIN players p ON ps.player_id = p.player_id
               LEFT JOIN teams t ON p.current_team_id = t.team_id
               WHERE p.full_name_ja IS NOT NULL
               ORDER BY ps.stat_group, p.full_name_ja"""
        ).fetchall()

    hitters: list[dict] = []
    pitchers: list[dict] = []
    for r in stat_rows:
        try:
            stat = json.loads(r["stats_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        entry = {
            "player_id": r["player_id"],
            "full_name": r["full_name"],
            "name_ja": r["full_name_ja"],
            "team_ja": r["team_ja"] or "-",
            "team_abbr": r["team_abbr"] or "",
            "stat": stat,
        }
        if r["stat_group"] == "hitting":
            # 打席ゼロは除外（投手の登板無関係なappearance回避）
            if (stat.get("plateAppearances") or 0) > 0:
                hitters.append(entry)
        elif r["stat_group"] == "pitching":
            if (stat.get("gamesPlayed") or 0) > 0:
                pitchers.append(entry)

    # OPS 降順で並べる（取り扱いやすさ）
    def _to_float(v: str | None, default: float = -1.0) -> float:
        try:
            return float(v) if v else default
        except (TypeError, ValueError):
            return default

    hitters.sort(key=lambda x: _to_float(x["stat"].get("ops")), reverse=True)
    pitchers.sort(key=lambda x: _to_float(x["stat"].get("era"), default=99.9))

    # リーダーボードに名前がある日本人選手の順位一覧
    with connect() as conn:
        leader_rows = conn.execute(
            """SELECT l.*, p.full_name_ja
               FROM leaders l
               JOIN players p ON l.player_id = p.player_id
               WHERE p.full_name_ja IS NOT NULL AND l.season = ?
               ORDER BY p.full_name_ja, l.league_id, l.rank""",
            (SEASON,),
        ).fetchall()
    leader_appearances = []
    for r in leader_rows:
        leader_appearances.append({
            "name_ja": r["full_name_ja"],
            "player_name": r["player_name"],
            "category_label": CATEGORY_LABEL.get(r["category"], r["category"]),
            "scope_label": SCOPE_LABEL.get(r["league_id"], "?"),
            "rank": r["rank"],
            "value": r["value"],
        })
    return hitters, pitchers, leader_appearances


def _safe_float(v, default=0.0):
    try:
        return float(v) if v not in (None, "", "-") else default
    except (TypeError, ValueError):
        return default


def _ip_to_outs(ip_str: str | None) -> int:
    """5.2 (= 5回2/3) → 17 outs に変換。"""
    if not ip_str:
        return 0
    try:
        whole, _, frac = str(ip_str).partition(".")
        return int(whole) * 3 + int(frac or 0)
    except ValueError:
        return 0


def _outs_to_ip(outs: int) -> float:
    return outs // 3 + (outs % 3) * 0.1


def _calc_era(er: int, outs: int) -> str:
    if outs <= 0:
        return "-"
    return f"{(er * 27) / outs:.2f}"


def load_pitcher_data(player_id: int, teams: dict) -> dict | None:
    """1投手のページ用データを取得・集計して返す。"""
    with connect() as conn:
        player_row = conn.execute(
            "SELECT player_id, full_name, full_name_ja, current_team_id FROM players WHERE player_id=?",
            (player_id,),
        ).fetchone()
        if not player_row:
            return None

        season_row = conn.execute(
            """SELECT stats_json FROM player_season_stats
               WHERE player_id=? AND season=? AND stat_group='pitching'""",
            (player_id, SEASON),
        ).fetchone()

        game_rows = conn.execute(
            """SELECT g.*, gm.venue FROM player_game_log g
               LEFT JOIN games gm ON g.game_pk = gm.game_pk
               WHERE g.player_id=? AND g.season=? AND g.stat_group='pitching'
               ORDER BY g.game_date""",
            (player_id, SEASON),
        ).fetchall()

        split_rows = conn.execute(
            """SELECT split_type, split_key, stats_json
               FROM player_split_stats
               WHERE player_id=? AND season=? AND stat_group='pitching'""",
            (player_id, SEASON),
        ).fetchall()

        # roster info for jersey
        roster_row = conn.execute(
            """SELECT jersey_number FROM rosters
               WHERE player_id=? AND season=?
               LIMIT 1""",
            (player_id, SEASON),
        ).fetchone()

    season_stat = json.loads(season_row["stats_json"]) if season_row else None

    # 派生指標
    derived = {"k9": "-", "bb9": "-", "k_bb": "-"}
    if season_stat:
        outs = _ip_to_outs(season_stat.get("inningsPitched"))
        if outs > 0:
            k = season_stat.get("strikeOuts") or 0
            bb = season_stat.get("baseOnBalls") or 0
            derived["k9"] = f"{(k * 27) / outs:.2f}"
            derived["bb9"] = f"{(bb * 27) / outs:.2f}"
            derived["k_bb"] = f"{(k / bb):.2f}" if bb > 0 else "∞"

    # gameLog
    game_log = []
    for r in game_rows:
        try:
            stat = json.loads(r["stats_json"])
        except json.JSONDecodeError:
            stat = {}
        opp = teams.get(r["opponent_id"])
        game_log.append({
            "game_pk": r["game_pk"],
            "game_date": r["game_date"],
            "is_home": r["is_home"],
            "is_win": r["is_win"],
            "opponent_ja": opp["name_ja"] if opp else "-",
            "venue": r["venue"],
            "stat": stat,
        })

    # splits 整理
    by_month_raw: dict[str, dict] = {}
    home_away_raw: dict[str, dict] = {}
    vs_hand_raw: dict[str, dict] = {}
    for r in split_rows:
        try:
            stat = json.loads(r["stats_json"])
        except json.JSONDecodeError:
            continue
        if r["split_type"] == "byMonth":
            by_month_raw[r["split_key"]] = stat
        elif r["split_type"] == "homeAndAway":
            home_away_raw[r["split_key"]] = stat
        elif r["split_type"] == "vsHand":
            vs_hand_raw[r["split_key"]] = stat

    by_month = [
        {"label": k, "stat": v}
        for k, v in sorted(by_month_raw.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 99)
    ]
    home_away = [{"key": k, "stat": home_away_raw[k]} for k in ("home", "away") if k in home_away_raw]
    vs_hand = [{"key": k, "stat": vs_hand_raw[k]} for k in ("vsLeft", "vsRight") if k in vs_hand_raw]

    # gameLog から対チーム・球場の集計を作る
    opp_agg: dict[int, dict] = {}
    venue_agg: dict[str, dict] = {}
    for g in game_log:
        s = g["stat"]
        outs = _ip_to_outs(s.get("inningsPitched"))
        er = s.get("earnedRuns") or 0
        runs = s.get("runs") or 0
        k = s.get("strikeOuts") or 0
        bb = s.get("baseOnBalls") or 0

        opp_id = next((k for k, v in teams.items() if v["name_ja"] == g["opponent_ja"]), None)
        if g["opponent_ja"] != "-":
            key = g["opponent_ja"]
            d = opp_agg.setdefault(key, {"opponent_ja": key, "games": 0, "outs": 0, "k": 0, "bb": 0, "r": 0, "er": 0})
            d["games"] += 1
            d["outs"] += outs
            d["k"] += k
            d["bb"] += bb
            d["r"] += runs
            d["er"] += er

        if g["venue"]:
            short = venue_short(g["venue"])
            d = venue_agg.setdefault(short, {"venue": short, "venue_full": g["venue"], "games": 0, "outs": 0, "k": 0, "bb": 0, "r": 0, "er": 0})
            d["games"] += 1
            d["outs"] += outs
            d["k"] += k
            d["bb"] += bb
            d["r"] += runs
            d["er"] += er

    def finalize(d):
        d["ip"] = _outs_to_ip(d["outs"])
        d["era"] = _calc_era(d["er"], d["outs"])
        return d

    by_opponent = sorted([finalize(d) for d in opp_agg.values()],
                         key=lambda x: x["games"], reverse=True)
    by_venue = sorted([finalize(d) for d in venue_agg.values()],
                      key=lambda x: x["games"], reverse=True)

    team = teams.get(player_row["current_team_id"]) or {}
    name_ja = player_row["full_name_ja"]
    return {
        "player_id": player_id,
        "full_name": player_row["full_name"],
        "name_ja": name_ja,
        "name_main": name_ja or player_row["full_name"],
        "team": team,
        "jersey_number": (roster_row or {})["jersey_number"] if roster_row else None,
        "season_stat": season_stat,
        "derived": derived,
        "game_log": game_log,
        "by_month": by_month,
        "home_away": home_away,
        "vs_hand": vs_hand,
        "by_opponent": by_opponent,
        "by_venue": by_venue,
    }


def build_pitcher_pages(env, base_ctx, teams: dict) -> int:
    """player_game_log にデータが入っている全投手のページを生成。"""
    with connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT player_id FROM player_game_log
               WHERE season=? AND stat_group='pitching'""",
            (SEASON,),
        ).fetchall()
    count = 0
    for r in rows:
        data = load_pitcher_data(r["player_id"], teams)
        if not data or not data["team"]:
            continue
        ctx = {**base_ctx, "active": None, "root": "../", "season": SEASON, **data}
        out = SITE / "players" / "pitchers" / f"{r['player_id']}.html"
        render(env, "player_pitcher.html", ctx, out)
        count += 1
    return count


def build_japanese_page(env, base_ctx) -> None:
    hitters, pitchers, leader_appearances = load_japanese_player_stats()
    ctx = {
        **base_ctx,
        "active": "japanese",
        "hitters": hitters,
        "pitchers": pitchers,
        "leader_appearances": leader_appearances,
    }
    render(env, "japanese.html", ctx, SITE / "japanese.html")


def build_leaders_page(env, base_ctx) -> None:
    scopes = []
    for scope_id, scope_label in [(0, "MLB全体"), (103, "ア・リーグ"), (104, "ナ・リーグ")]:
        cat_rows = load_leaders(scope_id)
        hitting_blocks = []
        for cat_key, cat_label, unit in LEADER_HITTING:
            rows = cat_rows.get(cat_key, [])[:10]
            hitting_blocks.append({"label": cat_label, "unit": unit, "rows": rows})
        pitching_blocks = []
        for cat_key, cat_label, unit in LEADER_PITCHING:
            rows = cat_rows.get(cat_key, [])[:10]
            pitching_blocks.append({"label": cat_label, "unit": unit, "rows": rows})
        scopes.append({
            "id": scope_id,
            "label": scope_label,
            "hitting": hitting_blocks,
            "pitching": pitching_blocks,
        })

    render(env, "leaders.html", {**base_ctx, "active": "leaders", "scopes": scopes}, SITE / "leaders.html")


def main() -> None:
    env = get_env()
    SITE.mkdir(parents=True, exist_ok=True)
    copy_static()

    now_jst = dt.datetime.now(JST)
    today = now_jst.date()
    updated_at = now_jst.strftime("%Y-%m-%d %H:%M JST")

    teams = load_teams()
    standings = load_standings_map()
    stand_by_div = load_standings_by_division()
    leagues, flat_divs = build_leagues_view(stand_by_div)

    base_ctx = {
        "season": SEASON,
        "updated_at": updated_at,
        "root": "",
    }

    print("[build] index")
    build_index(env, base_ctx, today, leagues, flat_divs)
    print("[build] standings")
    build_standings(env, base_ctx, leagues)
    print("[build] teams index")
    build_teams_index(env, base_ctx, leagues)
    print(f"[build] 30 team pages")
    build_team_pages(env, base_ctx, teams, standings, today)
    print("[build] game pages")
    build_game_pages(env, base_ctx, teams)
    print("[build] leaders")
    build_leaders_page(env, base_ctx)
    print("[build] japanese players")
    build_japanese_page(env, base_ctx)
    print("[build] pitcher pages")
    n = build_pitcher_pages(env, base_ctx, teams)
    print(f"  → {n} pitcher pages")

    print(f"\nBuild complete. Output: {SITE}")


if __name__ == "__main__":
    main()
