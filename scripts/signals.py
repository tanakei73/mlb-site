"""投手・チームの「調子」指標を計算する共通モジュール。

build_site.py（表示）と predict.py（予想反映）の両方から使う。
DB の player_game_log / standings を参照する。
"""
from __future__ import annotations

import json
from typing import Optional

from db import connect


def pitcher_form(player_id: int) -> Optional[dict]:
    """先発投手の調子指標を返す。

    返り値:
      {
        "gs": 登板数,
        "wins": 登板時チーム勝利数, "losses": 敗北数,
        "win_pct": 登板時チーム勝率 (float, 0-1),
        "streak_type": "W" | "L" | None,   # 直近の連勝/連敗
        "streak_len": 連続数 (int),
        "last5": ["W","L",...] 直近5登板（新しい順）,
        "hot": bool,   # 絶好調 (3連勝以上 or 勝率.700以上で5登板以上)
        "cold": bool,  # 不調 (3連敗以上 or 勝率.300以下で5登板以上)
      }
    データが無い場合は None。
    """
    if not player_id:
        return None
    with connect() as conn:
        rows = conn.execute(
            """SELECT is_win FROM player_game_log
               WHERE player_id=? AND stat_group='pitching'
               ORDER BY game_date""",
            (player_id,),
        ).fetchall()
    results = [r["is_win"] for r in rows if r["is_win"] in (0, 1)]
    if not results:
        return None

    wins = sum(1 for w in results if w == 1)
    losses = sum(1 for w in results if w == 0)
    total = wins + losses
    win_pct = wins / total if total else 0.0

    # 直近の連勝/連敗（末尾から）
    last = results[-1]
    streak_len = 0
    for w in reversed(results):
        if w == last:
            streak_len += 1
        else:
            break
    streak_type = "W" if last == 1 else "L"

    # 直近5登板（新しい順）
    last5 = ["W" if w == 1 else "L" for w in reversed(results[-5:])]

    hot = (streak_type == "W" and streak_len >= 3) or (total >= 5 and win_pct >= 0.70)
    cold = (streak_type == "L" and streak_len >= 3) or (total >= 5 and win_pct <= 0.30)

    return {
        "gs": len(results),
        "wins": wins,
        "losses": losses,
        "win_pct": round(win_pct, 3),
        "streak_type": streak_type,
        "streak_len": streak_len,
        "last5": last5,
        "hot": hot,
        "cold": cold,
    }


def pitcher_form_badge(form: Optional[dict]) -> Optional[dict]:
    """調子を1つの短いラベルに要約。表示用。

    返り値: {"label": "3連勝中", "kind": "hot"|"cold"|"neutral"} or None
    """
    if not form:
        return None
    if form["streak_type"] == "W" and form["streak_len"] >= 2:
        return {"label": f"{form['streak_len']}連勝中", "kind": "hot"}
    if form["streak_type"] == "L" and form["streak_len"] >= 2:
        return {"label": f"{form['streak_len']}連敗中", "kind": "cold"}
    # 連勝連敗が無ければ勝率ベース
    if form["gs"] >= 4:
        if form["win_pct"] >= 0.65:
            return {"label": f"登板時勝率.{int(form['win_pct']*1000):03d}", "kind": "hot"}
        if form["win_pct"] <= 0.35:
            return {"label": f"登板時勝率.{int(form['win_pct']*1000):03d}", "kind": "cold"}
    return {"label": f"{form['wins']}勝{form['losses']}敗", "kind": "neutral"}
