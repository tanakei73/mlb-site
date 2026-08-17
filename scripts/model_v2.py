"""検証済み予想モデル v2。

1328試合のリーク無しバックテスト(時系列で訓練/検証を分離)で選ばれた構成。
「その試合の直前までの情報だけ」で予想し、未知データで評価した結果:

    常にホームを選ぶ        51.7%
    チーム成績のみ          52.8%
    + 先発投手 (このモデル)  54.5%   ← 採用
    + 救援 + 勢い           53.6%   ← 過学習で悪化したため不採用

その後データが1693試合に増えた時点での再検証(拡張窓・未知1355試合):
    チーム成績のみ(k=0)     52.2%
    + 先発投手(k=0.5)       53.4%   ← 現行

構成:
    logit(p) = logit(Log5(ピタゴラス勝率)) + log(ホーム補正) + 0.50 × 先発の質の差

先発の重みの再検証(2026-08-18, 検証1355試合・拡張窓):
「エースが投げるのに数値が上がらない」という指摘を受けて再検証した。
当初は検証データ532試合で k=0.25 を選んでいたが、データが増えると劣っていた。

    k      的中率   対数尤度   本命(65%以上)の試合数と的中率
    0.25   52.8%   -0.6893    95試合 65.3%   ← 旧設定
    0.50   53.4%   -0.6890   133試合 66.9%   ← 採用
    0.75   54.1%   -0.6901   196試合 62.8%
    1.00   54.5%   -0.6925   264試合 62.1%

k=1.0 は全体の的中率は最良だが、確信度が膨らんで本命が264試合まで増え、
その的中率は62.1%に落ちる(校正が悪化)。k=0.5 は対数尤度が最良で、
本命の試合数が増えつつ的中率も上がるため、こちらを採用した。
なお的中率の差はMcNemar検定では有意ではない(k=0.5 vs 0.25 で p≈0.53)。
採用理由は主に校正の良さと本命ピックの質。

確信度で絞ると的中率が上がることも確認済み。閾値は下記の通り。
実際の的中率は pick_record.py が今季データから毎回再計算するので、
このモジュールの数値は序盤のフォールバック用。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Optional

from db import connect

HFA_ODDS = 1.137        # ホーム勝率53.2%相当
TEAM_PRIOR = 30         # チーム勝率をリーグ平均へ回帰させる仮想試合数
SP_PRIOR_OUTS = 60      # 先発の成績をリーグ平均へ回帰させる仮想アウト(約20回)
SP_WEIGHT = 0.50        # 先発の重み。下の再検証で 0.25 から引き上げた
MIN_GAMES = 20          # これ未満の消化試合数だと成績が不安定
CONFIDENT_THRESHOLD = 65  # 「本命」の基準(実績は pick_record が随時算出)
WATCH_THRESHOLD = 62      # 「注目」の基準

# 確信度帯ごとの的中率のフォールバック値(k=0.5での再検証時、未知1355試合)。
# 通常は pick_record.py が今季の実測値を毎回再計算するので、
# 実績が30試合未満の序盤だけこの値を使う。
BACKTEST_HIT_RATES = {62: 63.0, 65: 66.9}


def _log5(a: float, b: float) -> float:
    den = a + b - 2 * a * b
    return 0.5 if den == 0 else (a - a * b) / den


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _pythag(rs: float, ra: float) -> float:
    if rs + ra == 0:
        return 0.5
    return rs ** 1.83 / (rs ** 1.83 + ra ** 1.83)


class ModelData:
    """チーム成績と先発投手成績をまとめて読み込むキャッシュ。

    未来の試合を予想する用途なので「現時点までの全Final試合」を使う。
    (バックテストと同じ計算式で、情報の先取りは無い)
    """

    def __init__(self, before_date: Optional[str] = None):
        self.team = {}       # team_id -> {"g","rs","ra","pythag"}
        self.sp = {}         # player_id -> {"outs","runs","ra9"}
        self.league_ra9 = 4.45
        self._load(before_date)

    def _load(self, before_date: Optional[str]) -> None:
        where = "WHERE status='Final' AND home_score IS NOT NULL"
        params: tuple = ()
        if before_date:
            where += " AND game_date < ?"
            params = (before_date,)
        with connect() as c:
            games = c.execute(
                f"""SELECT home_team_id, away_team_id, home_score, away_score
                    FROM games {where}""", params).fetchall()
            plog_where = "WHERE stat_group='pitching'"
            if before_date:
                plog_where += " AND game_date < ?"
            plog = c.execute(
                f"""SELECT player_id, stats_json FROM player_game_log {plog_where}""",
                params if before_date else ()).fetchall()

        agg = defaultdict(lambda: {"g": 0, "rs": 0, "ra": 0})
        for g in games:
            h, a = g["home_team_id"], g["away_team_id"]
            hs, as_ = g["home_score"], g["away_score"]
            agg[h]["g"] += 1; agg[h]["rs"] += hs; agg[h]["ra"] += as_
            agg[a]["g"] += 1; agg[a]["rs"] += as_; agg[a]["ra"] += hs
        for tid, v in agg.items():
            raw = _pythag(v["rs"], v["ra"])
            v["pythag"] = (raw * v["g"] + 0.5 * TEAM_PRIOR) / (v["g"] + TEAM_PRIOR)
            self.team[tid] = v

        sp = defaultdict(lambda: {"outs": 0, "runs": 0})
        tot_o = tot_r = 0
        for r in plog:
            try:
                s = json.loads(r["stats_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            o = s.get("outs") or 0
            ru = s.get("runs") or 0
            sp[r["player_id"]]["outs"] += o
            sp[r["player_id"]]["runs"] += ru
            tot_o += o; tot_r += ru
        if tot_o:
            self.league_ra9 = tot_r * 27 / tot_o
        for pid, v in sp.items():
            if v["outs"]:
                raw = v["runs"] * 27 / v["outs"]
                v["ra9"] = ((raw * v["outs"] + self.league_ra9 * SP_PRIOR_OUTS)
                            / (v["outs"] + SP_PRIOR_OUTS))
            else:
                v["ra9"] = self.league_ra9
            self.sp[pid] = v

    def sp_ra9(self, pid: Optional[int]) -> tuple:
        """先発の9イニング換算失点(回帰済)と、実績イニング数を返す。"""
        if not pid or pid not in self.sp:
            return self.league_ra9, 0.0
        v = self.sp[pid]
        return v["ra9"], v["outs"] / 3


_shared: Optional["ModelData"] = None


def shared_data() -> "ModelData":
    """ビルド中に使い回す ModelData。全試合+全投手ログを読むので毎回作らない。"""
    global _shared
    if _shared is None:
        _shared = ModelData()
    return _shared


def predict_v2(game: dict, data: Optional[ModelData] = None) -> Optional[dict]:
    """検証済みモデルで1試合を予想。

    game には home_team_id / away_team_id / home_pitcher_id / away_pitcher_id が必要。
    返り値: {"home","away","confidence","fav_side","is_confident", ...}
    """
    data = data or shared_data()
    h = data.team.get(game.get("home_team_id"))
    a = data.team.get(game.get("away_team_id"))
    if not h or not a or h["g"] < MIN_GAMES or a["g"] < MIN_GAMES:
        return None

    base = _logit(_log5(h["pythag"], a["pythag"])) + math.log(HFA_ODDS)
    h_ra9, h_ip = data.sp_ra9(game.get("home_pitcher_id"))
    a_ra9, a_ip = data.sp_ra9(game.get("away_pitcher_id"))
    # 相手先発の方が失点しやすければホーム有利
    sp_edge = (a_ra9 - h_ra9) / data.league_ra9
    p_home = _sigmoid(base + SP_WEIGHT * sp_edge)

    home_pct = round(p_home * 100)
    away_pct = 100 - home_pct
    conf = max(home_pct, away_pct)
    # 先発が未定だとリーグ平均で埋めるため、確信度が中央に寄る(暫定値)
    provisional = not (game.get("home_pitcher_id") and game.get("away_pitcher_id"))
    tier = ("confident" if conf >= CONFIDENT_THRESHOLD
            else "watch" if conf >= WATCH_THRESHOLD else None)
    return {
        "home": home_pct,
        "away": away_pct,
        "confidence": conf,
        "fav_side": "home" if home_pct >= away_pct else "away",
        "tier": tier,
        "provisional": provisional,
        "is_confident": conf >= CONFIDENT_THRESHOLD,
        "team_edge": round((h["pythag"] - a["pythag"]) * 100, 1),
        "sp_edge": round(sp_edge * 100, 1),
        "home_sp_ra9": round(h_ra9, 2),
        "away_sp_ra9": round(a_ra9, 2),
        "home_sp_ip": round(h_ip, 1),
        "away_sp_ip": round(a_ip, 1),
        "league_ra9": round(data.league_ra9, 2),
    }


def expected_hit_rate(conf: int) -> Optional[float]:
    """確信度に対応する、バックテスト実測の的中率を返す。"""
    best = None
    for th in sorted(BACKTEST_HIT_RATES):
        if conf >= th:
            best = BACKTEST_HIT_RATES[th]
    return best
