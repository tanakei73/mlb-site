"""本命ピックの実績を、毎回その場で再計算する。

CI が実行のたびに DB を作り直すため「何を本命にしたか」を保存しておけない。
そこで記録を残す代わりに、過去の全 Final 試合について
「その試合の直前までのデータだけで予想したら何を選んでいたか」を
毎回再現し、実際の結果と突き合わせる。

model_v2 と同じ式・同じ閾値を使い、情報の先取りが無いよう日付順に
成績を積み上げながら計算する(walk-forward)ため、
バックテストと同じ条件の成績がそのままサイトに出る。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Optional

from db import connect
from model_v2 import (
    CONFIDENT_THRESHOLD,
    HFA_ODDS,
    MIN_GAMES,
    SP_PRIOR_OUTS,
    SP_WEIGHT,
    TEAM_PRIOR,
    WATCH_THRESHOLD,
    _log5,
    _logit,
    _pythag,
    _sigmoid,
)


def _load():
    with connect() as c:
        games = [dict(r) for r in c.execute(
            """SELECT g.game_pk, g.game_date, g.away_team_id, g.home_team_id,
                      g.away_score, g.home_score,
                      g.away_pitcher_id, g.home_pitcher_id,
                      g.away_pitcher, g.home_pitcher,
                      ta.name_ja away_ja, ta.abbreviation away_abbr,
                      th.name_ja home_ja, th.abbreviation home_abbr
               FROM games g
               LEFT JOIN teams ta ON g.away_team_id=ta.team_id
               LEFT JOIN teams th ON g.home_team_id=th.team_id
               WHERE g.status='Final' AND g.home_score IS NOT NULL
                 AND g.away_score IS NOT NULL
               ORDER BY g.game_date, g.game_pk""")]
        plog = defaultdict(list)
        for r in c.execute(
            """SELECT player_id, game_date, stats_json FROM player_game_log
               WHERE stat_group='pitching' ORDER BY game_date"""):
            try:
                s = json.loads(r["stats_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            plog[r["player_id"]].append(
                (r["game_date"], s.get("outs") or 0, s.get("runs") or 0))
    return games, plog


def _sp_history(plog):
    """投手ごとに (日付, その日より前の累計outs, 累計runs) の列を作る。"""
    hist = {}
    for pid, entries in plog.items():
        o = r = 0
        rows = []
        for d, eo, er in entries:
            rows.append((d, o, r))   # この登板の「前」の累計
            o += eo; r += er
        rows.append(("9999-99-99", o, r))
        hist[pid] = rows
    return hist


def _sp_ra9(hist, pid, date, league_ra9):
    if not pid or pid not in hist:
        return league_ra9
    outs = runs = 0
    for d, o, r in hist[pid]:
        if d >= date:
            outs, runs = o, r
            break
    else:
        outs, runs = hist[pid][-1][1], hist[pid][-1][2]
    if outs == 0:
        return league_ra9
    raw = runs * 27 / outs
    return (raw * outs + league_ra9 * SP_PRIOR_OUTS) / (outs + SP_PRIOR_OUTS)


def pick_record(limit_recent: int = 12) -> Optional[dict]:
    """本命/注目それぞれの通算成績と、直近ピックの明細を返す。"""
    games, plog = _load()
    if not games:
        return None
    hist = _sp_history(plog)
    tot_o = sum(e[1] for v in plog.values() for e in v)
    tot_r = sum(e[2] for v in plog.values() for e in v)
    league_ra9 = tot_r * 27 / tot_o if tot_o else 4.45

    W = defaultdict(int); L = defaultdict(int)
    RS = defaultdict(int); RA = defaultdict(int)
    picks = []
    for g in games:
        h, a = g["home_team_id"], g["away_team_id"]
        hg, ag = W[h] + L[h], W[a] + L[a]
        if hg >= MIN_GAMES and ag >= MIN_GAMES:
            hpy = _pythag(RS[h], RA[h]); apy = _pythag(RS[a], RA[a])
            hpy = (hpy * hg + .5 * TEAM_PRIOR) / (hg + TEAM_PRIOR)
            apy = (apy * ag + .5 * TEAM_PRIOR) / (ag + TEAM_PRIOR)
            base = _logit(_log5(hpy, apy)) + math.log(HFA_ODDS)
            h_ra9 = _sp_ra9(hist, g["home_pitcher_id"], g["game_date"], league_ra9)
            a_ra9 = _sp_ra9(hist, g["away_pitcher_id"], g["game_date"], league_ra9)
            p_home = _sigmoid(base + SP_WEIGHT * (a_ra9 - h_ra9) / league_ra9)

            home_pct = round(p_home * 100)
            conf = max(home_pct, 100 - home_pct)
            tier = ("confident" if conf >= CONFIDENT_THRESHOLD
                    else "watch" if conf >= WATCH_THRESHOLD else None)
            if tier:
                fav_home = home_pct >= 50
                won = ((g["home_score"] > g["away_score"]) if fav_home
                       else (g["away_score"] > g["home_score"]))
                picks.append({
                    "game_pk": g["game_pk"], "date": g["game_date"],
                    "tier": tier, "confidence": conf, "won": won,
                    "fav_ja": g["home_ja"] if fav_home else g["away_ja"],
                    "fav_abbr": g["home_abbr"] if fav_home else g["away_abbr"],
                    "opp_ja": g["away_ja"] if fav_home else g["home_ja"],
                    "fav_is_home": fav_home,
                    "score": (f"{g['home_score']}-{g['away_score']}" if fav_home
                              else f"{g['away_score']}-{g['home_score']}"),
                })

        hs, as_ = g["home_score"], g["away_score"]
        RS[h] += hs; RA[h] += as_; RS[a] += as_; RA[a] += hs
        if hs > as_:
            W[h] += 1; L[a] += 1
        else:
            W[a] += 1; L[h] += 1

    if not picks:
        return None

    def _summ(items, expected):
        n = len(items)
        w = sum(1 for p in items if p["won"])
        return {
            "total": n, "win": w, "loss": n - w,
            "pct": round(w / n * 100, 1),
            "expected": expected,
            "diff": round(w / n * 100 - expected, 1),
        }

    conf_items = [p for p in picks if p["tier"] == "confident"]
    watch_items = [p for p in picks if p["tier"] == "watch"]
    picks.sort(key=lambda p: p["date"], reverse=True)
    return {
        "confident": _summ(conf_items, 68.5) if conf_items else None,
        "watch": _summ(watch_items, 66.3) if watch_items else None,
        "all": _summ(picks, 67.0),
        "recent": picks[:limit_recent],
        "confident_threshold": CONFIDENT_THRESHOLD,
        "watch_threshold": WATCH_THRESHOLD,
    }
