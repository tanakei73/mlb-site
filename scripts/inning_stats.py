"""イニング別の得点傾向を集計する。

linescore の innings_json から「何回の、表/裏に、どれだけ点が入るか」を出す。

注意点:
- 9回裏はホームがリードしていると行われないため、機会数が他より大幅に少ない。
  「接戦の9回裏」だけを集めた偏ったサンプルなので、他の回と同列に比較できない。
- 延長は特別ルール(無死二塁)で条件が違うので9回までを対象にする。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Optional

from db import connect

MAX_INNING = 9


def _mean_se(values: list[int]) -> tuple:
    n = len(values)
    if n == 0:
        return 0.0, 0.0, 0
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / n
    return m, math.sqrt(var / n), n


def inning_scoring() -> Optional[dict]:
    """イニング別(表/裏)の得点傾向をまとめて返す。"""
    with connect() as conn:
        rows = conn.execute(
            """SELECT b.innings_json FROM boxscore_linescore b
               JOIN games g ON b.game_pk = g.game_pk
               WHERE g.status='Final' AND b.innings_json IS NOT NULL""").fetchall()
    if not rows:
        return None

    half = defaultdict(list)     # (inning, side) -> [runs...]
    games = 0
    for r in rows:
        try:
            innings = json.loads(r["innings_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not innings:
            continue
        games += 1
        for i in innings:
            num = i.get("num")
            if not num or num > MAX_INNING:
                continue
            for side in ("away", "home"):
                runs = (i.get(side) or {}).get("runs")
                if runs is not None:
                    half[(num, side)].append(int(runs))

    if not half:
        return None

    def _pack(vals):
        m, se, n = _mean_se(vals)
        scored = sum(1 for v in vals if v > 0)
        big = sum(1 for v in vals if v >= 3)
        return {
            "mean": round(m, 3), "se": round(se, 4), "n": n,
            "ci": round(1.96 * se, 3),
            "score_pct": round(scored / n * 100, 1) if n else 0,
            "big_pct": round(big / n * 100, 1) if n else 0,
        }

    innings = []
    for num in range(1, MAX_INNING + 1):
        away = half.get((num, "away"), [])
        home = half.get((num, "home"), [])
        both = away + home
        if not both:
            continue
        a, h, b = _pack(away), _pack(home), _pack(both)
        # 表裏差が有意か
        diff = h["mean"] - a["mean"]
        sed = math.sqrt(a["se"] ** 2 + h["se"] ** 2)
        z = diff / sed if sed else 0
        innings.append({
            "inning": num, "away": a, "home": h, "both": b,
            "home_edge": round(diff, 3),
            "home_edge_z": round(z, 2),
            "home_edge_sig": abs(z) > 1.96,
            # 9回裏は機会が少ない(ホームリード時は行われない)
            "partial": h["n"] < a["n"] * 0.9,
        })

    ranked = sorted(innings, key=lambda x: -x["both"]["mean"])
    best, worst = ranked[0], ranked[-1]
    d = best["both"]["mean"] - worst["both"]["mean"]
    sed = math.sqrt(best["both"]["se"] ** 2 + worst["both"]["se"] ** 2)

    # 半イニング単位の順位(1回裏などを直接比べる)
    halves = []
    for x in innings:
        for side, label in (("away", "表"), ("home", "裏")):
            halves.append({
                "inning": x["inning"], "side": side, "label": f"{x['inning']}回{label}",
                "partial": x["partial"] and side == "home",
                **x[side],
            })
    halves.sort(key=lambda x: -x["mean"])

    league_mean = sum(v for vals in half.values() for v in vals) / sum(
        len(vals) for vals in half.values())
    all_home = [v for (n_, s), vals in half.items() if s == "home" for v in vals]
    all_away = [v for (n_, s), vals in half.items() if s == "away" for v in vals]

    return {
        "games": games,
        "innings": innings,
        "ranked": ranked,
        "halves": halves,
        "top_halves": halves[:5],
        "league_mean": round(league_mean, 3),
        "home_baseline": round(sum(all_home) / len(all_home) - sum(all_away) / len(all_away), 3),
        "spread_z": round(d / sed, 2) if sed else 0,
        "spread_sig": abs(d / sed) > 1.96 if sed else False,
        "best": best, "worst": worst,
        "spread": round(d, 3),
    }
