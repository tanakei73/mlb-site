"""MLB 30球団の日本語名マスタ。team_id は MLB Stats API の teamId に対応。"""
from __future__ import annotations

TEAM_JA: dict[int, dict[str, str]] = {
    # American League East
    110: {"name_ja": "オリオールズ",   "division_ja": "ア・リーグ東地区"},
    111: {"name_ja": "レッドソックス", "division_ja": "ア・リーグ東地区"},
    147: {"name_ja": "ヤンキース",     "division_ja": "ア・リーグ東地区"},
    139: {"name_ja": "レイズ",         "division_ja": "ア・リーグ東地区"},
    141: {"name_ja": "ブルージェイズ", "division_ja": "ア・リーグ東地区"},
    # American League Central
    145: {"name_ja": "ホワイトソックス", "division_ja": "ア・リーグ中地区"},
    114: {"name_ja": "ガーディアンズ",   "division_ja": "ア・リーグ中地区"},
    116: {"name_ja": "タイガース",       "division_ja": "ア・リーグ中地区"},
    118: {"name_ja": "ロイヤルズ",       "division_ja": "ア・リーグ中地区"},
    142: {"name_ja": "ツインズ",         "division_ja": "ア・リーグ中地区"},
    # American League West
    117: {"name_ja": "アストロズ",     "division_ja": "ア・リーグ西地区"},
    108: {"name_ja": "エンゼルス",     "division_ja": "ア・リーグ西地区"},
    133: {"name_ja": "アスレチックス", "division_ja": "ア・リーグ西地区"},
    136: {"name_ja": "マリナーズ",     "division_ja": "ア・リーグ西地区"},
    140: {"name_ja": "レンジャーズ",   "division_ja": "ア・リーグ西地区"},
    # National League East
    144: {"name_ja": "ブレーブス",   "division_ja": "ナ・リーグ東地区"},
    146: {"name_ja": "マーリンズ",   "division_ja": "ナ・リーグ東地区"},
    121: {"name_ja": "メッツ",       "division_ja": "ナ・リーグ東地区"},
    143: {"name_ja": "フィリーズ",   "division_ja": "ナ・リーグ東地区"},
    120: {"name_ja": "ナショナルズ", "division_ja": "ナ・リーグ東地区"},
    # National League Central
    112: {"name_ja": "カブス",       "division_ja": "ナ・リーグ中地区"},
    113: {"name_ja": "レッズ",       "division_ja": "ナ・リーグ中地区"},
    158: {"name_ja": "ブリュワーズ", "division_ja": "ナ・リーグ中地区"},
    134: {"name_ja": "パイレーツ",   "division_ja": "ナ・リーグ中地区"},
    138: {"name_ja": "カージナルス", "division_ja": "ナ・リーグ中地区"},
    # National League West
    109: {"name_ja": "ダイヤモンドバックス", "division_ja": "ナ・リーグ西地区"},
    115: {"name_ja": "ロッキーズ",           "division_ja": "ナ・リーグ西地区"},
    119: {"name_ja": "ドジャース",           "division_ja": "ナ・リーグ西地区"},
    135: {"name_ja": "パドレス",             "division_ja": "ナ・リーグ西地区"},
    137: {"name_ja": "ジャイアンツ",         "division_ja": "ナ・リーグ西地区"},
}

LEAGUE_JA = {103: "アメリカン・リーグ", 104: "ナショナル・リーグ"}


def name_ja(team_id: int, fallback: str) -> str:
    info = TEAM_JA.get(team_id)
    return info["name_ja"] if info else fallback


def division_ja(team_id: int, fallback: str) -> str:
    info = TEAM_JA.get(team_id)
    return info["division_ja"] if info else fallback


def league_ja(league_id: int) -> str:
    return LEAGUE_JA.get(league_id, "")
