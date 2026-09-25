"""ポストシーズンのシード表とブラケットを組み立てる。

MLB API は対戦相手が決まっていない枠も「AL Wild Card #3」のような
ダミーチームで返すため、開幕前からブラケットの形を出せる。
勝ち上がりが決まると同じ game_pk のままチームIDが実チームに差し替わる。

シリーズ勝率について:
    1試合ごとの予想は model_v2（レギュラーシーズンで検証したもの）を
    先発投手抜き＝チーム力とホームアドバンテージだけで出し、
    実際の開催順（どちらの本拠地か）に沿って畳み込んでいる。
    ただしポストシーズンは
      - エースが中3〜4日で投げる
      - 救援の使い方がレギュラーシーズンと別物
      - 消化試合が無い
    ため、この数字はレギュラーシーズンほど当てにならない。
    テンプレート側にもその注意書きを出している。
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from db import connect
from model_v2 import shared_data, predict_v2

JST = dt.timezone(dt.timedelta(hours=9))

ROUNDS = [
    ("F", "ワイルドカードシリーズ", "3戦2勝"),
    ("D", "ディビジョンシリーズ", "5戦3勝"),
    ("L", "リーグ優勝決定シリーズ", "7戦4勝"),
    ("W", "ワールドシリーズ", "7戦4勝"),
]
LG = {"AL": "ア・リーグ", "NL": "ナ・リーグ"}
DIV = {"East": "東", "Central": "中", "West": "西"}
STATE_JA = {
    "Scheduled": "予定", "Pre-Game": "まもなく", "Warmup": "まもなく",
    "In Progress": "試合中", "Final": "終了", "Game Over": "終了",
    "Postponed": "中止", "Suspended": "中断", "Cancelled": "中止",
}


def _label_ja(name: Optional[str]) -> str:
    """ダミーチーム名を日本語にする。実チームはそのまま返す。"""
    if not name:
        return "未定"
    m = re.match(r"^(AL|NL) Wild Card #(\d)$", name)
    if m:
        return f"{LG[m.group(1)]} WC{m.group(2)}位"
    m = re.match(r"^(AL|NL) (East|Central|West) #1$", name)
    if m:
        return f"{LG[m.group(1)]}{DIV[m.group(2)]}地区1位"
    m = re.match(r"^(AL|NL) (\d)/(\d) Winner$", name)
    if m:
        return f"{LG[m.group(1)]} {m.group(2)}位×{m.group(3)}位の勝者"
    m = re.match(r"^(AL|NL) (Higher|Lower) Seed$", name)
    if m:
        return f"{LG[m.group(1)]} {'上位' if m.group(2) == 'Higher' else '下位'}シード"
    m = re.match(r"^(Higher|Lower) Seed League Champion$", name)
    if m:
        return f"{'上位' if m.group(1) == 'Higher' else '下位'}シードのリーグ優勝チーム"
    return name


def seeds() -> Optional[dict]:
    """順位表からシードを組む。地区優勝3チームが1〜3位、残りの上位3チームが4〜6位。"""
    with connect() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT s.*, t.name_ja, t.abbreviation, t.league_id
               FROM standings s JOIN teams t USING(team_id)""")]
    if not rows:
        return None
    out = {}
    for lg_id, key in ((104, "NL"), (103, "AL")):
        ts = [t for t in rows if t["league_id"] == lg_id]
        if not ts:
            continue
        div = sorted([t for t in ts if t["division_rank"] == 1], key=lambda t: -(t["wins"] or 0))
        wc = sorted([t for t in ts if t["division_rank"] != 1], key=lambda t: -(t["wins"] or 0))
        lst = []
        for i, t in enumerate(div[:3], 1):
            lst.append({**t, "seed": i, "kind": "地区優勝",
                        "bye": i <= 2, "left": 162 - (t["wins"] or 0) - (t["losses"] or 0)})
        for i, t in enumerate(wc[:3], 4):
            lst.append({**t, "seed": i, "kind": "ワイルドカード",
                        "bye": False, "left": 162 - (t["wins"] or 0) - (t["losses"] or 0)})
        # まだ可能性が残っているチーム（敗退が確定していない）
        alive = [t for t in wc[3:]
                 if str(t.get("elimination_number") or "").upper() not in ("E", "-", "")][:3]
        out[key] = {"name": LG[key], "seeds": lst, "alive": alive}
    return out or None


def _series_win_prob(pgames: list[float], need: int) -> float:
    """1試合ごとの勝率から、シリーズを勝つ確率を出す（開催順のまま畳み込む）。"""
    memo: dict = {}

    def rec(i: int, w: int, l: int) -> float:
        if w >= need:
            return 1.0
        if l >= need:
            return 0.0
        if i >= len(pgames):
            return 0.0
        k = (i, w, l)
        if k not in memo:
            p = pgames[i]
            memo[k] = p * rec(i + 1, w + 1, l) + (1 - p) * rec(i + 1, w, l + 1)
        return memo[k]

    return rec(0, 0, 0)


def bracket() -> Optional[list]:
    """ラウンドごとのシリーズ一覧を返す。"""
    with connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM postseason_games ORDER BY game_datetime, series_game_no")]
        real = {r["team_id"]: dict(r) for r in c.execute(
            "SELECT team_id, name_ja, abbreviation FROM teams")}
    if not rows:
        return None
    data = shared_data()

    groups: dict = {}
    for g in rows:
        key = (g["game_type"], tuple(sorted((g["away_team_id"] or 0, g["home_team_id"] or 0))))
        groups.setdefault(key, []).append(g)

    out = []
    for gtype, label, fmt in ROUNDS:
        series = []
        for (t, _pair), gs in groups.items():
            if t != gtype:
                continue
            gs.sort(key=lambda g: (g["game_datetime"] or "", g["series_game_no"] or 0))
            first = gs[0]
            hi, lo = first["home_team_id"], first["away_team_id"]   # 第1戦のホーム＝上位シード
            hi_real, lo_real = real.get(hi), real.get(lo)

            hw = lw = 0
            games = []
            for g in gs:
                played = g["home_score"] is not None and g["away_score"] is not None
                win_side = None
                if played:
                    home_won = g["home_score"] > g["away_score"]
                    win_side = ("hi" if home_won else "lo") if g["home_team_id"] == hi \
                        else ("lo" if home_won else "hi")
                    if win_side == "hi":
                        hw += 1
                    else:
                        lw += 1
                t_jst = None
                if g["game_datetime"]:
                    t_jst = dt.datetime.fromisoformat(
                        g["game_datetime"].replace("Z", "+00:00")).astimezone(JST)
                games.append({
                    "no": g["series_game_no"], "date": t_jst, "status": g["status"],
                    "tbd": bool(g.get("time_tbd")),
                    "detailed": STATE_JA.get(g["detailed_state"], g["detailed_state"]),
                    "venue": g["venue"],
                    "hi_home": g["home_team_id"] == hi,
                    "hi_score": g["home_score"] if g["home_team_id"] == hi else g["away_score"],
                    "lo_score": g["away_score"] if g["home_team_id"] == hi else g["home_score"],
                    "played": played, "win_side": win_side,
                    "away_pitcher": g["away_pitcher"], "home_pitcher": g["home_pitcher"],
                })

            need = (first["series_total"] or 7) // 2 + 1
            prob = None
            if hi_real and lo_real:
                pg = []
                for g in gs:
                    p = predict_v2({"home_team_id": g["home_team_id"],
                                    "away_team_id": g["away_team_id"],
                                    "home_pitcher_id": None, "away_pitcher_id": None}, data)
                    if not p:
                        pg = []
                        break
                    pg.append((p["home"] if g["home_team_id"] == hi else p["away"]) / 100)
                if pg:
                    prob = round(_series_win_prob(pg, need) * 100)

            lg_key = (first["series_name"] or "")[:2]
            series.append({
                "name": first["series_name"], "abbr": first["series_abbr"],
                "league": lg_key,
                "league_ja": LG.get(lg_key, "ワールドシリーズ"),
                "format": fmt, "need": need,
                "hi": {"id": hi, "name": (hi_real or {}).get("name_ja") or _label_ja(first["home_name"]),
                       "abbr": (hi_real or {}).get("abbreviation"), "real": bool(hi_real),
                       "wins": hw, "prob": prob},
                "lo": {"id": lo, "name": (lo_real or {}).get("name_ja") or _label_ja(first["away_name"]),
                       "abbr": (lo_real or {}).get("abbreviation"), "real": bool(lo_real),
                       "wins": lw, "prob": (100 - prob) if prob is not None else None},
                "games": games,
                "decided": hw >= need or lw >= need,
                "winner": "hi" if hw >= need else ("lo" if lw >= need else None),
                "start": games[0]["date"] if games else None,
            })
        series.sort(key=lambda s: (s["league"] != "NL", s["start"] or dt.datetime.max.replace(tzinfo=JST)))
        if series:
            out.append({"type": gtype, "label": label, "format": fmt, "series": series})
    return out or None


def build_postseason() -> Optional[dict]:
    s, b = seeds(), bracket()
    if not b and not s:
        return None
    return {"seeds": s, "bracket": b}
