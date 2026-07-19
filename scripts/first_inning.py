"""第一イニング（1回）の勝敗確率を予測する。

各サイドの1回の得点を Poisson 分布でモデル化し、
- 打線の1回平均得点（team_first_score）
- 相手先発の1回平均失点（player_split_stats の byInning）
を「リーグ平均に対する掛け算」で合成して期待値 λ を出す。

  λ_away(表の攻撃) = 打線_away_1回平均得点 × 相手先発(home)_1回平均失点 / リーグ平均
  λ_home(裏の攻撃) = 打線_home_1回平均得点 × 相手先発(away)_1回平均失点 / リーグ平均

その後、両サイドを独立 Poisson として1回終了時の勝敗確率を計算する。
"""
from __future__ import annotations

import json
import math
from typing import Optional

from db import connect

LEAGUE_MEAN_1ST_RUNS = 0.54   # 1回の平均得点（実測 ≈ 0.543）
MAX_RUNS = 8                    # Poisson 畳み込みの打ち切り


def _team_first(team_id: Optional[int]) -> Optional[dict]:
    if not team_id:
        return None
    with connect() as conn:
        r = conn.execute(
            """SELECT games, first_inn_runs_scored rs, first_inn_runs_allowed ra,
                      first_inn_scored fs, first_inn_allowed fa
               FROM team_first_score WHERE team_id=? AND season=(
                   SELECT MAX(season) FROM team_first_score)""",
            (team_id,)).fetchone()
    if not r or not r["games"]:
        return None
    g = r["games"]
    return {
        "games": g,
        "mean_scored": (r["rs"] or 0) / g,      # 1回平均得点
        "mean_allowed": (r["ra"] or 0) / g,     # 1回平均失点
        "score_rate": (r["fs"] or 0) / g,       # 1回得点率(二値)
        "allow_rate": (r["fa"] or 0) / g,       # 1回失点率(二値)
    }


def pitcher_first_inning(player_id: Optional[int]) -> Optional[dict]:
    """先発の1回失点の傾向。byInning split(1回) + 先発数から算出。

    返り値: {"starts": n, "mean_allowed": 1回平均失点, "allow_prob": 1回失点確率}
    """
    if not player_id:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT stats_json FROM player_split_stats
               WHERE player_id=? AND split_type='byInning' AND split_key='1'
               AND season=(SELECT MAX(season) FROM player_split_stats)""",
            (player_id,)).fetchone()
        # 先発数: 4回以上投げた登板を先発とみなす
        starts_row = conn.execute(
            """SELECT COUNT(*) n FROM player_game_log
               WHERE player_id=? AND stat_group='pitching'""",
            (player_id,)).fetchone()
    if not row:
        return None
    try:
        s = json.loads(row["stats_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    runs = s.get("runs")
    starts = starts_row["n"] if starts_row else 0
    if not starts or runs is None:
        return None
    # リーグ平均への回帰: 仮想的に K 登板ぶんリーグ平均を足す
    # (小サンプルの投手が「初回失点0%」など極端値にならないように)
    K = 5
    mean_allowed = (runs + LEAGUE_MEAN_1ST_RUNS * K) / (starts + K)
    return {
        "starts": starts,
        "raw_mean": runs / starts,
        "mean_allowed": mean_allowed,
        # ポアソン近似で「1回に1点以上取られる確率」
        "allow_prob": 1.0 - math.exp(-mean_allowed),
    }


def _poisson_pmf(lam: float, k: int) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _blend_lambda(team_mean: Optional[float], pitcher_mean: Optional[float]) -> float:
    """打線の1回平均得点 × 相手先発の1回平均失点 / リーグ平均。
    どちらか欠けたら残りを使う。両方欠けたらリーグ平均。"""
    if team_mean is not None and pitcher_mean is not None:
        lam = team_mean * pitcher_mean / LEAGUE_MEAN_1ST_RUNS
    elif team_mean is not None:
        lam = team_mean
    elif pitcher_mean is not None:
        lam = pitcher_mean
    else:
        lam = LEAGUE_MEAN_1ST_RUNS
    return max(0.05, min(3.0, lam))   # 極端値をクランプ


def predict_first_inning(game: dict) -> Optional[dict]:
    """1試合の第一イニング予想を返す。game は away_team_id/home_team_id/
    away_pitcher_id/home_pitcher_id を含む dict。"""
    away = _team_first(game.get("away_team_id"))
    home = _team_first(game.get("home_team_id"))
    if not away or not home:
        return None
    ap = pitcher_first_inning(game.get("away_pitcher_id"))
    hp = pitcher_first_inning(game.get("home_pitcher_id"))

    # 表(away攻撃) = away打線 × home先発失点 / リーグ
    lam_away = _blend_lambda(away["mean_scored"], hp["mean_allowed"] if hp else None)
    # 裏(home攻撃) = home打線 × away先発失点 / リーグ
    lam_home = _blend_lambda(home["mean_scored"], ap["mean_allowed"] if ap else None)

    # 各サイドの得点分布 (0..MAX_RUNS)
    pa = [_poisson_pmf(lam_away, k) for k in range(MAX_RUNS + 1)]
    ph = [_poisson_pmf(lam_home, k) for k in range(MAX_RUNS + 1)]

    p_away_ahead = p_home_ahead = p_tie = 0.0
    for i in range(MAX_RUNS + 1):
        for j in range(MAX_RUNS + 1):
            p = pa[i] * ph[j]
            if i > j:
                p_away_ahead += p
            elif j > i:
                p_home_ahead += p
            else:
                p_tie += p
    total = p_away_ahead + p_home_ahead + p_tie
    if total > 0:
        p_away_ahead /= total; p_home_ahead /= total; p_tie /= total

    return {
        "away_score_prob": round((1 - math.exp(-lam_away)) * 100),  # 表に得点する確率
        "home_score_prob": round((1 - math.exp(-lam_home)) * 100),
        "away_lambda": round(lam_away, 2),
        "home_lambda": round(lam_home, 2),
        "away_ahead": round(p_away_ahead * 100),   # 1回終了時リード
        "home_ahead": round(p_home_ahead * 100),
        "tie": round(p_tie * 100),
        "away_pitcher_allow_prob": round(ap["allow_prob"] * 100) if ap else None,
        "home_pitcher_allow_prob": round(hp["allow_prob"] * 100) if hp else None,
    }
