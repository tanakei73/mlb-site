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

LEAGUE_MEAN_1ST_RUNS = 0.53   # 1回の平均得点（実測 ≈ 0.530）
MAX_RUNS = 12                   # 分布の畳み込みの打ち切り

# 1回の得点は「0点か、まとめて大量点」に偏る(過分散)ため、ポアソンでは表せない。
# 実測(3690サイド)は 平均0.530 / 分散1.074 で、分散比 2.03。
# 負の二項分布 (var = mu + mu^2/r) を当てはめると r ≈ 0.517 でよく一致する。
#
#   得点     実測    負の二項   ポアソン
#   0点     70.7%    69.4%    58.9%
#   1点     15.8%    18.2%    31.2%
#   4点以上  2.5%     2.2%     0.2%
#   1点以上 29.3%    30.6%    41.1%   ← ポアソンは+11.8ptの過大評価
NB_DISPERSION = 0.517


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
        # 「1回に1点以上取られる確率」(負の二項分布)
        "allow_prob": _score_prob(mean_allowed),
    }


def _nb_pmf(mu: float, k: int, r: float = NB_DISPERSION) -> float:
    """負の二項分布の確率質量。平均 mu、分散 mu + mu^2/r。

    1回の得点はポアソンより「0点」と「大量点」に偏るため、こちらを使う。
    """
    return (math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1))
            * (r / (r + mu)) ** r * (mu / (r + mu)) ** k)


def _score_prob(mu: float) -> float:
    """1点以上取る確率(= 1 - P(0点))。"""
    return 1.0 - _nb_pmf(mu, 0)


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
    # 下限0.25(=得点率18%)。打線と投手の1回成績は小サンプルで振れやすく、
    # 掛け算だと極端に低い値が出てしまう。実測でも「得点率20%未満」と
    # 予想した場面の実際の得点率は19.5%あり、一桁%はあり得ない。
    # 下限0.05→0.25でBrierが 0.2027→0.2006 に改善することを確認済み。
    return max(0.25, min(3.0, lam))


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
    pa = [_nb_pmf(lam_away, k) for k in range(MAX_RUNS + 1)]
    ph = [_nb_pmf(lam_home, k) for k in range(MAX_RUNS + 1)]

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
        "away_score_prob": round(_score_prob(lam_away) * 100),  # 表に得点する確率
        "home_score_prob": round(_score_prob(lam_home) * 100),
        "away_lambda": round(lam_away, 2),
        "home_lambda": round(lam_home, 2),
        "away_ahead": round(p_away_ahead * 100),   # 1回終了時リード
        "home_ahead": round(p_home_ahead * 100),
        "tie": round(p_tie * 100),
        "away_pitcher_allow_prob": round(ap["allow_prob"] * 100) if ap else None,
        "home_pitcher_allow_prob": round(hp["allow_prob"] * 100) if hp else None,
    }


# ---- 事前スナップショット & 的中率トラッカー ----

GAP_MIN = 15      # 「差がついた予想」と見なすリード確率差(%)。これ未満は五分カード扱い
GAP_STRONG = 30   # 強く差がついた予想の閾値


def save_first_inning_snapshot(game_pk: int, fi: dict, predicted_at: str,
                               backfill: bool = False) -> None:
    """試合前の第一イニング予想を凍結保存。既存があれば上書きしない。

    backfill=True は「試合後に現在データで事後推定した近似値」を意味する
    (真の事前スナップショットが無い過去試合の穴埋め用)。
    """
    if not fi:
        return
    with connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO first_inning_predictions
               (game_pk, predicted_at, away_ahead, tie, home_ahead, backfill)
               VALUES (?,?,?,?,?,?)""",
            (game_pk, predicted_at, fi["away_ahead"], fi["tie"], fi["home_ahead"],
             1 if backfill else 0),
        )
        conn.commit()


def _first_inning_actual(innings_json: Optional[str]) -> Optional[tuple]:
    """innings_json から (away_runs_1回, home_runs_1回) を返す。無ければ None。"""
    if not innings_json:
        return None
    try:
        innings = json.loads(innings_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if not innings:
        return None
    first = innings[0]
    a = (first.get("away") or {}).get("runs")
    h = (first.get("home") or {}).get("runs")
    if a is None or h is None:
        return None
    return int(a), int(h)


def first_inning_baseline() -> Optional[dict]:
    """比較用の基準値。「予想せず選んだ場合」に何%になるかを実データから出す。

    1回は半分以上が0-0で終わるため「負けなければ的中」は何もしなくても高く出る。
    モデルの実質的な上積みを示すために併記する。
    """
    with connect() as conn:
        rows = conn.execute(
            """SELECT b.innings_json FROM boxscore_linescore b
               JOIN games g ON b.game_pk=g.game_pk
               WHERE g.status='Final' AND b.innings_json IS NOT NULL""").fetchall()
    tie = away = home = 0
    for r in rows:
        actual = _first_inning_actual(r["innings_json"])
        if actual is None:
            continue
        a, h = actual
        if a == h:
            tie += 1
        elif a > h:
            away += 1
        else:
            home += 1
    n = tie + away + home
    if not n:
        return None
    return {
        "games": n,
        "tie_pct": round(tie / n * 100, 1),
        # 「負けなければ的中」の基準値
        "nolose_random": round((tie + (away + home) / 2) / n * 100, 1),
        "nolose_home": round((tie + home) / n * 100, 1),
        "nolose_away": round((tie + away) / n * 100, 1),
        # 「先制できた率」の基準値
        "strike_random": round((away + home) / 2 / n * 100, 1),
        "strike_home": round(home / n * 100, 1),
        "strike_away": round(away / n * 100, 1),
    }


def first_inning_accuracy(gap_min: int = GAP_MIN) -> Optional[dict]:
    """保存済みスナップショットと1回の実績を照合し的中率を集計。

    判定: リード差が gap_min 以上ついた予想のみ対象。有利と出た側が
    実際の1回で「リード維持 or 引き分け(0-0含む)」なら的中(負けなければ的中)、
    ビハインドなら外れ。
    返り値に overall / strong(差>=30) の集計と、直近試合の明細リストを含む。
    """
    sql = """
        SELECT f.away_ahead, f.tie, f.home_ahead, f.backfill,
               g.game_pk, g.game_date, g.status,
               ta.name_ja AS away_ja, th.name_ja AS home_ja,
               b.innings_json
        FROM first_inning_predictions f
        JOIN games g ON f.game_pk = g.game_pk
        JOIN boxscore_linescore b ON f.game_pk = b.game_pk
        LEFT JOIN teams ta ON g.away_team_id = ta.team_id
        LEFT JOIN teams th ON g.home_team_id = th.team_id
        WHERE g.status = 'Final'
        ORDER BY g.game_datetime DESC
    """
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql)]

    details = []
    for r in rows:
        actual = _first_inning_actual(r["innings_json"])
        if actual is None:
            continue
        a_runs, h_runs = actual
        gap = abs(r["away_ahead"] - r["home_ahead"])
        if gap < gap_min:
            continue   # 五分カードは予想していないので対象外
        # 有利と出た側
        fav_home = r["home_ahead"] > r["away_ahead"]
        fav_ja = r["home_ja"] if fav_home else r["away_ja"]
        fav_runs = h_runs if fav_home else a_runs
        opp_runs = a_runs if fav_home else h_runs
        if fav_runs > opp_runs:
            outcome = "win"     # 有利側が初回に先制
        elif fav_runs == opp_runs:
            outcome = "tie"     # 0-0 など五分 → 負けてはいない
        else:
            outcome = "loss"    # ビハインド → 外れ
        details.append({
            "game_pk": r["game_pk"],
            "date": r["game_date"],
            "away_ja": r["away_ja"], "home_ja": r["home_ja"],
            "fav_ja": fav_ja, "fav_home": fav_home,
            "gap": gap,
            "away_runs": a_runs, "home_runs": h_runs,
            "outcome": outcome,
            "hit": outcome in ("win", "tie"),
            "backfill": bool(r["backfill"]),
        })

    if not details:
        return None

    def _summ(items):
        w = sum(1 for d in items if d["outcome"] == "win")
        t = sum(1 for d in items if d["outcome"] == "tie")
        l = sum(1 for d in items if d["outcome"] == "loss")
        n = len(items)
        return {
            "total": n, "win": w, "tie": t, "loss": l,
            "hit": w + t,
            "hit_pct": round((w + t) / n * 100),
            "win_pct": round(w / n * 100),
        }

    strong = [d for d in details if d["gap"] >= GAP_STRONG]
    overall = _summ(details)
    strong_s = _summ(strong) if strong else None
    base = first_inning_baseline()

    # 有利予想がホーム側かビジター側かで分けた内訳。
    # 1回はホームの方が先制しやすい(裏の攻撃)ため、基準値も side ごとに変わる。
    def _side_split(items):
        out = {}
        for is_home, key in ((True, "home"), (False, "away")):
            sub = [d for d in items if d["fav_home"] == is_home]
            if not sub:
                continue
            s = _summ(sub)
            if base:
                s["edge_nolose"] = round(
                    s["hit_pct"] - base["nolose_home" if is_home else "nolose_away"], 1)
                s["edge_strike"] = round(
                    s["win_pct"] - base["strike_home" if is_home else "strike_away"], 1)
            out[key] = s
        return out

    split = {"gap_min": _side_split(details)}
    if strong:
        split["gap_strong"] = _side_split(strong)
    # モデルの実質的な上積み(基準値との差)
    if base:
        overall["edge_nolose"] = round(overall["hit_pct"] - base["nolose_home"], 1)
        overall["edge_strike"] = round(overall["win_pct"] - base["strike_home"], 1)
        if strong_s:
            strong_s["edge_nolose"] = round(strong_s["hit_pct"] - base["nolose_home"], 1)
            strong_s["edge_strike"] = round(strong_s["win_pct"] - base["strike_home"], 1)
    return {
        "overall": overall,
        "strong": strong_s,
        "baseline": base,
        "split": split,
        "gap_min": gap_min,
        "gap_strong": GAP_STRONG,
        "details": details,
        "has_backfill": any(d["backfill"] for d in details),
    }
