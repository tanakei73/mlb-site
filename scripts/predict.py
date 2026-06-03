"""試合のホーム勝率予想ロジック。

「予測モデル」ではなく、複数指標を線形合成した「目安スコア」。
- 先発投手の質 (ERA, WHIP) vs リーグ平均
- チーム打撃力 (OPS) vs リーグ平均
- チーム投球力 (ERA) vs リーグ平均
- 先発のその相手チームに対する過去実績
- ホームアドバンテージ (+4pt 固定)
- 最終的に 20-80% の範囲にクランプ
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from db import connect
from venue_factor import venue_factor

# MLB リーグ平均（暫定値・必要なら DB の team_season_stats 平均から計算）
LEAGUE_AVG_ERA = 4.10
LEAGUE_AVG_WHIP = 1.27
LEAGUE_AVG_OPS = 0.720
HOME_ADVANTAGE = 4.0  # MLBは過去20年で平均 54% vs 46%


@dataclass
class PredictionInput:
    away_pitcher_era: Optional[float]
    away_pitcher_whip: Optional[float]
    home_pitcher_era: Optional[float]
    home_pitcher_whip: Optional[float]
    away_team_ops: Optional[float]
    away_team_era: Optional[float]
    home_team_ops: Optional[float]
    home_team_era: Optional[float]
    away_matchup_era: Optional[float]   # その先発の対home_team 過去ERA
    home_matchup_era: Optional[float]   # その先発の対away_team 過去ERA
    venue_factor: float = 1.0           # 1.0=中立 / >1=打高 / <1=投高
    away_last_ten: Optional[str] = None # "6-4" 形式
    home_last_ten: Optional[str] = None


@dataclass
class Prediction:
    home_prob: int       # 0-100
    away_prob: int
    components: dict     # スコア内訳


def _safe_float(v) -> Optional[float]:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_player_pitching(player_id: int) -> tuple[Optional[float], Optional[float]]:
    if not player_id:
        return None, None
    with connect() as conn:
        row = conn.execute(
            """SELECT stats_json FROM player_season_stats
               WHERE player_id=? AND stat_group='pitching'""",
            (player_id,),
        ).fetchone()
    if not row:
        return None, None
    try:
        s = json.loads(row["stats_json"])
    except json.JSONDecodeError:
        return None, None
    return _safe_float(s.get("era")), _safe_float(s.get("whip"))


def _load_team_stats(team_id: int, group: str) -> dict | None:
    if not team_id:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT stats_json FROM team_season_stats
               WHERE team_id=? AND stat_group=?""",
            (team_id, group),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["stats_json"])
    except json.JSONDecodeError:
        return None


def _load_last_ten(team_id: int) -> Optional[str]:
    if not team_id:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT last_ten FROM standings WHERE team_id=?", (team_id,)
        ).fetchone()
    return row["last_ten"] if row else None


def _load_matchup_era(pitcher_id: int, opponent_team_id: int) -> Optional[float]:
    """その先発が opponent_team に対して過去登板した時の自責点合計/IP からERAを計算。"""
    if not pitcher_id or not opponent_team_id:
        return None
    with connect() as conn:
        rows = conn.execute(
            """SELECT stats_json FROM player_game_log
               WHERE player_id=? AND opponent_id=? AND stat_group='pitching'""",
            (pitcher_id, opponent_team_id),
        ).fetchall()
    if not rows:
        return None
    total_outs = 0
    total_er = 0
    for r in rows:
        try:
            s = json.loads(r["stats_json"])
        except json.JSONDecodeError:
            continue
        ip_str = s.get("inningsPitched")
        if not ip_str:
            continue
        try:
            whole, _, frac = str(ip_str).partition(".")
            outs = int(whole) * 3 + int(frac or 0)
        except ValueError:
            continue
        er = s.get("earnedRuns") or 0
        total_outs += outs
        total_er += er
    if total_outs == 0:
        return None
    return (total_er * 27) / total_outs


def build_input(game: dict) -> PredictionInput:
    away_era, away_whip = _load_player_pitching(game.get("away_pitcher_id"))
    home_era, home_whip = _load_player_pitching(game.get("home_pitcher_id"))

    away_hit = _load_team_stats(game["away_team_id"], "hitting") or {}
    away_pit = _load_team_stats(game["away_team_id"], "pitching") or {}
    home_hit = _load_team_stats(game["home_team_id"], "hitting") or {}
    home_pit = _load_team_stats(game["home_team_id"], "pitching") or {}

    away_matchup = _load_matchup_era(game.get("away_pitcher_id"), game["home_team_id"])
    home_matchup = _load_matchup_era(game.get("home_pitcher_id"), game["away_team_id"])

    return PredictionInput(
        away_pitcher_era=away_era,
        away_pitcher_whip=away_whip,
        home_pitcher_era=home_era,
        home_pitcher_whip=home_whip,
        away_team_ops=_safe_float(away_hit.get("ops")),
        away_team_era=_safe_float(away_pit.get("era")),
        home_team_ops=_safe_float(home_hit.get("ops")),
        home_team_era=_safe_float(home_pit.get("era")),
        away_matchup_era=away_matchup,
        home_matchup_era=home_matchup,
        venue_factor=venue_factor(game.get("venue")),
        away_last_ten=_load_last_ten(game.get("away_team_id")),
        home_last_ten=_load_last_ten(game.get("home_team_id")),
    )


def predict(game: dict) -> Prediction:
    """1試合のホーム/ビジター勝率予想を返す。"""
    pin = build_input(game)

    def pitcher_factor(era, whip):
        if era is None and whip is None:
            return 0.0
        e = era if era is not None else LEAGUE_AVG_ERA
        w = whip if whip is not None else LEAGUE_AVG_WHIP
        # 先発の質：ERA -2点（実値ベース）, WHIP -10点。両方良いと +20 程度
        return (LEAGUE_AVG_ERA - e) * 2.5 + (LEAGUE_AVG_WHIP - w) * 12

    def batting_factor(ops):
        if ops is None:
            return 0.0
        return (ops - LEAGUE_AVG_OPS) * 80    # ±5pt 程度

    def defense_factor(team_era):
        if team_era is None:
            return 0.0
        return (LEAGUE_AVG_ERA - team_era) * 3   # ±5pt 程度

    def matchup_factor(era):
        # 対戦相手別実績はサンプル少のため控えめ ±3pt
        if era is None:
            return 0.0
        return max(-3.0, min(3.0, (LEAGUE_AVG_ERA - era) * 1.5))

    def momentum_factor(last_ten: Optional[str]) -> float:
        """直近10戦の勝ち越し差を ±3pt の補正に変換。"""
        if not last_ten or "-" not in last_ten:
            return 0.0
        try:
            w, l = last_ten.split("-")
            diff = int(w) - int(l)   # -10..+10
        except ValueError:
            return 0.0
        return max(-3.0, min(3.0, diff * 0.6))

    # 球場ファクター: 打高球場では投手効果を薄め、投高では強める
    # venue_factor 1.30 (Coors) → pitcher_weight ≈ 0.77
    # venue_factor 0.92 (Petco) → pitcher_weight ≈ 1.09
    pitcher_weight = 1.0 / max(0.5, min(1.5, pin.venue_factor))

    home_pitcher_pt = pitcher_factor(pin.home_pitcher_era, pin.home_pitcher_whip) * pitcher_weight
    away_pitcher_pt = pitcher_factor(pin.away_pitcher_era, pin.away_pitcher_whip) * pitcher_weight
    home_bat_pt = batting_factor(pin.home_team_ops)
    away_bat_pt = batting_factor(pin.away_team_ops)
    home_def_pt = defense_factor(pin.home_team_era)
    away_def_pt = defense_factor(pin.away_team_era)
    home_matchup_pt = matchup_factor(pin.home_matchup_era)
    away_matchup_pt = matchup_factor(pin.away_matchup_era)
    home_momentum_pt = momentum_factor(pin.home_last_ten)
    away_momentum_pt = momentum_factor(pin.away_last_ten)

    home_strength = (
        home_pitcher_pt + home_bat_pt + home_def_pt
        + home_matchup_pt + home_momentum_pt + HOME_ADVANTAGE
    )
    away_strength = (
        away_pitcher_pt + away_bat_pt + away_def_pt
        + away_matchup_pt + away_momentum_pt
    )

    diff = home_strength - away_strength
    home_prob = 50 + diff * 0.6
    home_prob = max(20, min(80, home_prob))
    away_prob = 100 - home_prob

    components = {
        "home_pitcher_pt":  round(home_pitcher_pt, 1),
        "away_pitcher_pt":  round(away_pitcher_pt, 1),
        "home_bat_pt":      round(home_bat_pt, 1),
        "away_bat_pt":      round(away_bat_pt, 1),
        "home_def_pt":      round(home_def_pt, 1),
        "away_def_pt":      round(away_def_pt, 1),
        "home_matchup_pt":  round(home_matchup_pt, 1),
        "away_matchup_pt":  round(away_matchup_pt, 1),
        "home_momentum_pt": round(home_momentum_pt, 1),
        "away_momentum_pt": round(away_momentum_pt, 1),
        "home_advantage":   HOME_ADVANTAGE,
        "pitcher_weight":   round(pitcher_weight, 2),
        "venue_factor":     pin.venue_factor,
        "input":            pin,
    }
    return Prediction(
        home_prob=int(round(home_prob)),
        away_prob=int(round(away_prob)),
        components=components,
    )


if __name__ == "__main__":
    # 簡易確認
    import datetime as dt
    JST = dt.timezone(dt.timedelta(hours=9))
    today_iso = dt.datetime.now(JST).date().isoformat()
    with connect() as conn:
        rows = conn.execute(
            """SELECT g.*, ta.name_ja AS away_ja, th.name_ja AS home_ja
               FROM games g
               LEFT JOIN teams ta ON g.away_team_id = ta.team_id
               LEFT JOIN teams th ON g.home_team_id = th.team_id
               WHERE g.game_date = ?""",
            (today_iso,),
        ).fetchall()
    for r in rows:
        g = dict(r)
        p = predict(g)
        print(f"{g['away_ja']:<10} {p.away_prob:>3}% - {p.home_prob:>3}% {g['home_ja']:<10}  "
              f"({g.get('away_pitcher')} vs {g.get('home_pitcher')})")
