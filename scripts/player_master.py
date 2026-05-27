"""日本人MLB選手の日本語名マスタ（フルネーム英語 -> 日本語）。

今後新しい選手がMLB入りした際は、ここに 1 行追加すれば
リーダーボード・ロースター・試合詳細すべてに反映される。
"""
from __future__ import annotations

# 英語フルネーム (MLB Stats API の personName.fullName) -> 日本語名
JP_PLAYER_JA: dict[str, str] = {
    # --- 投手 ---
    "Yu Darvish":          "ダルビッシュ有",
    "Yoshinobu Yamamoto":  "山本由伸",
    "Kodai Senga":         "千賀滉大",
    "Yusei Kikuchi":       "菊池雄星",
    "Kenta Maeda":         "前田健太",
    "Shintaro Fujinami":   "藤浪晋太郎",
    "Shota Imanaga":       "今永昇太",
    "Naoyuki Uwasawa":     "上沢直之",
    "Yuki Matsui":         "松井裕樹",
    "Roki Sasaki":         "佐々木朗希",
    "Tatsuya Imai":        "今井達也",
    "Yoshihisa Hirano":    "平野佳寿",
    "Shun Yamaguchi":      "山口俊",
    "Tomoyuki Sugano":     "菅野智之",
    "Hiroki Kuroda":       "黒田博樹",
    "Masahiro Tanaka":     "田中将大",
    "Daisuke Matsuzaka":   "松坂大輔",
    "Hideo Nomo":          "野茂英雄",
    "Hideki Okajima":      "岡島秀樹",
    "Hisashi Iwakuma":     "岩隈久志",

    # --- 野手・二刀流 ---
    "Shohei Ohtani":       "大谷翔平",
    "Seiya Suzuki":        "鈴木誠也",
    "Masataka Yoshida":    "吉田正尚",
    "Munetaka Murakami":   "村上宗隆",
    "Kazuma Okamoto":      "岡本和真",
    "Shogo Akiyama":       "秋山翔吾",
    "Ichiro Suzuki":       "イチロー",
    "Hideki Matsui":       "松井秀喜",
    "Kosuke Fukudome":     "福留孝介",
    "Norichika Aoki":      "青木宣親",
    "Tadahito Iguchi":     "井口資仁",
    "So Taguchi":          "田口壮",
    "Kazuo Matsui":        "松井稼頭央",

    # --- 日系（米国出身だが日本表記で親しまれる選手） ---
    "Lars Nootbaar":       "ヌートバー",
}


def player_name_ja(full_name: str | None) -> str | None:
    """英語フルネームから日本語名を返す。マップに無ければ None。"""
    if not full_name:
        return None
    return JP_PLAYER_JA.get(full_name)
