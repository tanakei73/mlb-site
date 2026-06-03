"""球場の hitter-friendly 係数 (1.00 = リーグ平均).

1.00 を超える ＝ 打高 (得点が出やすい / 投手不利)
1.00 を下回る ＝ 投高 (得点が出にくい / 投手有利)

数字は Baseball Savant / FanGraphs の Park Factor (Runs) を参考にした概算。
極端な球場だけ補正し、それ以外は 1.0 (中立) として無視する。
"""
from __future__ import annotations

VENUE_HITTER_FACTOR: dict[str, float] = {
    # === 打高 ===
    "Coors Field":                        1.30,  # ロッキーズ (標高1600m、別格)
    "Great American Ball Park":           1.10,  # レッズ
    "Wrigley Field":                      1.08,  # カブス (風次第)
    "Yankee Stadium":                     1.08,  # ヤンキース (短い右翼)
    "Fenway Park":                        1.06,  # レッドソックス (グリーンモンスター)
    "Citizens Bank Park":                 1.05,  # フィリーズ
    "Globe Life Field":                   1.03,  # レンジャーズ

    # === 投高 ===
    "Petco Park":                         0.92,  # パドレス
    "Oracle Park":                        0.92,  # ジャイアンツ
    "Tropicana Field":                    0.94,  # レイズ (旧本拠地)
    "Steinbrenner Field":                 0.95,  # レイズ仮本拠地
    "George M. Steinbrenner Field":       0.95,
    "Daikin Park":                        0.96,  # アストロズ (旧Minute Maid)
    "Minute Maid Park":                   0.96,
    "T-Mobile Park":                      0.94,  # マリナーズ
    "loanDepot park":                     0.93,  # マーリンズ
    "loanDepot Park":                     0.93,
    "Dodger Stadium":                     0.97,  # ドジャース
    "UNIQLO Field at Dodger Stadium":     0.97,
}


def venue_factor(name: str | None) -> float:
    if not name:
        return 1.0
    return VENUE_HITTER_FACTOR.get(name, 1.0)
