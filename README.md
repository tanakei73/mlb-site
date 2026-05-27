# MLB データ置き場

メジャーリーグの試合データ・順位・リーダーボードを日本語でまとめた静的サイト。
MLB Stats API から日次でデータを取得し、GitHub Actions で自動更新・GitHub Pages で公開しています。

公開URL: https://tanakei73.github.io/mlb-site/

## サイト構成

| ページ | 内容 |
|---|---|
| `/index.html` | 本日の試合 / 順位サマリ / 直近試合結果 |
| `/standings.html` | AL/NL × 6地区の順位表 |
| `/teams.html` | チーム一覧 |
| `/teams/[abbr].html` | 30チーム個別ページ（試合・日程・ロースター） |
| `/games/[gamePk].html` | 試合詳細（ラインスコア・チーム成績） |
| `/leaders.html` | リーダーボード（打撃7カテゴリ + 投手5カテゴリ × MLB/AL/NL） |
| `/japanese.html` | 日本人MLB選手のシーズン成績一覧 |

## ローカルでの開発

```bash
# 仮想環境セットアップ
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# データ取得（30秒〜2分）
python scripts/fetch.py

# HTML生成
python scripts/build_site.py

# ローカルサーバー起動
cd site && python3 -m http.server 8765
# → http://localhost:8765/
```

## ディレクトリ構成

```
mlb-site/
├── scripts/
│   ├── db.py              # SQLite スキーマ
│   ├── team_master.py     # チーム日本語名マスタ
│   ├── player_master.py   # 日本人選手日本語名マスタ
│   ├── fetch.py           # MLB API → SQLite
│   └── build_site.py      # SQLite → HTML
├── templates/             # Jinja2 テンプレート
├── static/style.css       # スタイルシート
├── data/mlb.db            # SQLite (git管理外)
├── site/                  # 生成HTML (git管理外)
└── .github/workflows/daily.yml  # 日次自動更新
```

## 日本人選手の追加方法

`scripts/player_master.py` の `JP_PLAYER_JA` に1行追加するだけで、
ロースター・リーダーボード・日本人選手ページすべてに自動反映されます。

```python
JP_PLAYER_JA = {
    ...
    "Shohei Ohtani": "大谷翔平",
    "新しい選手の英語名": "日本語名",   # ← ここに追加
}
```

## データソース

- [MLB Stats API](https://statsapi.mlb.com/) (公式・無料)
