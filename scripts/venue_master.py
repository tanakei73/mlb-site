"""MLB 30球団の本拠地球場・短縮表記マスタ。

API から返ってくる球場名（命名権スポンサー込みの長い名前）を、
表での視認性の良い短縮名に変換するためのマップ。
"""
from __future__ import annotations
import re

# 完全一致用マップ
VENUE_SHORT: dict[str, str] = {
    # AL East
    "Camden Yards":               "カムデン",
    "Oriole Park at Camden Yards":"カムデン",
    "Fenway Park":                "フェンウェイ",
    "Yankee Stadium":             "ヤンキースタジアム",
    "Tropicana Field":            "トロピカーナ",
    "Steinbrenner Field":         "スタインブレナー",
    "George M. Steinbrenner Field":"スタインブレナー",
    "Rogers Centre":              "ロジャースセンター",

    # AL Central
    "Rate Field":                 "Rate Field",
    "Guaranteed Rate Field":      "Rate Field",
    "Progressive Field":          "プログレッシブ",
    "Comerica Park":              "コメリカパーク",
    "Kauffman Stadium":           "カウフマン",
    "Target Field":               "ターゲット",

    # AL West
    "Minute Maid Park":           "ダイキンパーク",
    "Daikin Park":                "ダイキンパーク",
    "Angel Stadium":              "エンゼルスタジアム",
    "Sutter Health Park":         "サター・ヘルス",
    "Oakland Coliseum":           "オークランド",
    "T-Mobile Park":              "T-モバイル",
    "Globe Life Field":           "グローブライフ",

    # NL East
    "Truist Park":                "トゥルーイスト",
    "loanDepot park":             "ローンデポ",
    "loanDepot Park":             "ローンデポ",
    "Citi Field":                 "シティ・フィールド",
    "Citizens Bank Park":         "シチズンズバンク",
    "Nationals Park":             "ナショナルズパーク",

    # NL Central
    "Wrigley Field":              "リグレー",
    "Great American Ball Park":   "グレートアメリカン",
    "American Family Field":      "AFフィールド",
    "PNC Park":                   "PNCパーク",
    "Busch Stadium":              "ブッシュ",

    # NL West
    "Chase Field":                "チェイス",
    "Coors Field":                "クアーズ",
    "Dodger Stadium":             "ドジャースタジアム",
    "UNIQLO Field at Dodger Stadium": "ドジャースタジアム",
    "Petco Park":                 "ペトコ",
    "Oracle Park":                "オラクル",
}

# 末尾の冠スポンサーを剥がす保険（マップに無い場合の汎用処理）
_TRIM_PATTERNS = [
    re.compile(r"^[A-Z][A-Za-z0-9.\-&' ]+\s+at\s+(.+)$"),    # "X Field at Y" -> "Y"
    re.compile(r"^(.+)\s+presented by .+$", re.IGNORECASE),  # "X presented by Y" -> "X"
]


def venue_short(name: str | None) -> str:
    if not name:
        return ""
    if name in VENUE_SHORT:
        return VENUE_SHORT[name]
    # 汎用処理
    for pat in _TRIM_PATTERNS:
        m = pat.match(name)
        if m:
            inner = m.group(1).strip()
            if inner in VENUE_SHORT:
                return VENUE_SHORT[inner]
            return inner
    # それでも長い場合は最初の単語2つくらいに切る
    if len(name) > 18:
        return name[:16] + "…"
    return name
