"""注目している投手・チームの登板/試合結果をまとめる。

投手ページ(308枚)は「1人を深く」見るためのもの。こちらは逆に
「注目している数人を横に並べて見比べる」ためのページ。

各登板は、実際に賭けている項目に合わせて次の3つを持つ:
    勝敗          チームがその試合に勝ったか
    初回失点      その投手が1回に取られた点(linescore の1回から実測)
    5回まで合計   両チームの5回終了時点の合計得点

注目リストはこのファイル先頭の WATCH_PITCHERS / WATCH_TEAMS を編集する。
"""
from __future__ import annotations

import json
from typing import Optional

from db import connect

# --- 注目リスト（ここを編集する） -------------------------------------
# (player_id, 表示名)。表示名を None にすると DB の名前をそのまま使う。
WATCH_PITCHERS: list[tuple[int, Optional[str]]] = [
    (694819, "ミジオロウスキー"),
    (808967, None),               # 山本由伸（DBに日本語名あり）
    (650911, "C.サンチェス"),
    (666200, "ルサルド"),
    (543243, "S.グレイ"),
    (669373, "スクーバル"),
]

# 「乗りやすい」「警戒」はこれまでの実感によるメモ。集計には使わない。
WATCH_TEAMS: list[tuple[int, str]] = [
    (119, "trust"),   # ドジャース
    (158, "trust"),   # ブリュワーズ
    (143, "trust"),   # フィリーズ
    (108, "care"),    # エンゼルス
    (109, "care"),    # ダイヤモンドバックス
    (116, "care"),    # タイガース
    (113, "care"),    # レッズ
]
TEAM_NOTE = {"trust": "乗りやすい", "care": "警戒"}
# ---------------------------------------------------------------------

MIN_OUTS_FOR_START = 9    # 3回未満の登板は「先発」とみなさない(救援・雨天中断など)


def _innings_runs(innings_json: Optional[str], side: str,
                  upto: Optional[int] = None) -> Optional[int]:
    """linescore から片側の得点を合計する。upto を指定するとその回まで。"""
    if not innings_json:
        return None
    try:
        innings = json.loads(innings_json)
    except (TypeError, json.JSONDecodeError):
        return None
    total = 0
    for x in innings:
        num = x.get("num")
        if not num or (upto is not None and num > upto):
            continue
        half = x.get(side) or {}
        total += half.get("runs") or 0
    return total


def _both_runs(innings_json: Optional[str], upto: int) -> Optional[int]:
    h = _innings_runs(innings_json, "home", upto)
    a = _innings_runs(innings_json, "away", upto)
    return None if h is None or a is None else h + a


def pitcher_starts(player_id: int, display_name: Optional[str] = None) -> Optional[dict]:
    """1投手の先発登板を古い順に返す。"""
    with connect() as conn:
        row = conn.execute(
            """SELECT p.full_name, p.full_name_ja, t.name_ja team
               FROM players p LEFT JOIN teams t ON p.current_team_id=t.team_id
               WHERE p.player_id=?""", (player_id,)).fetchone()
        if not row:
            return None
        logs = conn.execute(
            """SELECT l.game_date, l.is_home, l.is_win, l.stats_json,
                      o.name_ja opp, o.abbreviation opp_abbr,
                      ls.innings_json
               FROM player_game_log l
               LEFT JOIN teams o ON l.opponent_id=o.team_id
               LEFT JOIN boxscore_linescore ls ON ls.game_pk=l.game_pk
               WHERE l.player_id=? AND l.stat_group='pitching'
               ORDER BY l.game_date""", (player_id,)).fetchall()

    starts = []
    for r in logs:
        try:
            s = json.loads(r["stats_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not s.get("gamesStarted") or (s.get("outs") or 0) < MIN_OUTS_FOR_START:
            continue
        is_home = bool(r["is_home"])
        # 投手が投げた側 = 相手の攻撃。ホーム先発なら表(away)が相手。
        opp_side = "away" if is_home else "home"
        first = _innings_runs(r["innings_json"], opp_side, upto=1)
        starts.append({
            "date": r["game_date"],
            "label": f'{int(r["game_date"][5:7])}/{int(r["game_date"][8:10])}',
            "is_home": is_home,
            "opp": r["opp"] or "?",
            "opp_abbr": r["opp_abbr"] or "?",
            # is_win はチームの勝敗。投手自身の勝敗は stats_json 側。
            "team_won": None if r["is_win"] == -1 else bool(r["is_win"]),
            "decision": ("W" if s.get("wins") else "L" if s.get("losses") else None),
            "ip": s.get("inningsPitched"),
            "runs": s.get("runs"),
            "er": s.get("earnedRuns"),
            "first_runs": first,
            "runs_thru5": _both_runs(r["innings_json"], 5),
        })
    if not starts:
        return None

    def _rate(items, pred):
        ok = [x for x in items if pred(x) is not None]
        return None if not ok else round(sum(1 for x in ok if pred(x)) / len(ok) * 100)

    home = [x for x in starts if x["is_home"]]
    away = [x for x in starts if not x["is_home"]]
    scored = [x for x in starts if x["first_runs"] is not None]
    return {
        "player_id": player_id,
        "name": display_name or row["full_name_ja"] or row["full_name"],
        "name_en": row["full_name"],
        "team": row["team"] or "",
        "starts": starts,
        "n": len(starts),
        "home_n": len(home), "away_n": len(away),
        "win_pct": _rate(starts, lambda x: x["team_won"]),
        "home_win_pct": _rate(home, lambda x: x["team_won"]),
        "away_win_pct": _rate(away, lambda x: x["team_won"]),
        # 初回無失点でしのいだ割合
        "first_clean_pct": (round(sum(1 for x in scored if x["first_runs"] == 0)
                                  / len(scored) * 100) if scored else None),
        "first_clean_n": sum(1 for x in scored if x["first_runs"] == 0),
        "first_n": len(scored),
    }


def team_recent(team_id: int, limit: int = 12) -> Optional[dict]:
    """注目チームの直近試合。"""
    with connect() as conn:
        t = conn.execute("SELECT name_ja, abbreviation FROM teams WHERE team_id=?",
                         (team_id,)).fetchone()
        if not t:
            return None
        rows = conn.execute(
            """SELECT g.game_date, g.home_team_id, g.away_team_id,
                      g.home_score, g.away_score, ls.innings_json,
                      th.name_ja home_ja, ta.name_ja away_ja,
                      th.abbreviation home_ab, ta.abbreviation away_ab
               FROM games g
               LEFT JOIN boxscore_linescore ls ON ls.game_pk=g.game_pk
               JOIN teams th ON g.home_team_id=th.team_id
               JOIN teams ta ON g.away_team_id=ta.team_id
               WHERE g.status='Final' AND g.home_score IS NOT NULL
                 AND (g.home_team_id=? OR g.away_team_id=?)
               ORDER BY g.game_date DESC LIMIT ?""",
            (team_id, team_id, limit)).fetchall()

    games = []
    for r in rows:
        is_home = r["home_team_id"] == team_id
        us, them = ((r["home_score"], r["away_score"]) if is_home
                    else (r["away_score"], r["home_score"]))
        our_side = "home" if is_home else "away"
        games.append({
            "date": r["game_date"],
            "label": f'{int(r["game_date"][5:7])}/{int(r["game_date"][8:10])}',
            "is_home": is_home,
            "opp": (r["away_ja"] if is_home else r["home_ja"]),
            "opp_abbr": (r["away_ab"] if is_home else r["home_ab"]),
            "won": us > them,
            "score": f"{us}-{them}",
            "first_scored": _innings_runs(r["innings_json"], our_side, upto=1),
            "runs_thru5": _both_runs(r["innings_json"], 5),
        })
    games.reverse()   # 古い順に並べ替え（表の左が古い）
    if not games:
        return None
    wins = sum(1 for g in games if g["won"])
    return {
        "team_id": team_id,
        "name": t["name_ja"],
        "abbr": t["abbreviation"],
        "games": games,
        "n": len(games),
        "wins": wins,
        "win_pct": round(wins / len(games) * 100),
    }


def build_watchlist() -> Optional[dict]:
    """ページ用のデータ一式。"""
    pitchers = [p for p in (pitcher_starts(pid, nm) for pid, nm in WATCH_PITCHERS) if p]
    teams = []
    for tid, note in WATCH_TEAMS:
        t = team_recent(tid)
        if t:
            t["note"] = note
            t["note_ja"] = TEAM_NOTE.get(note, "")
            teams.append(t)
    if not pitchers and not teams:
        return None
    return {"pitchers": pitchers, "teams": teams}
