# scripts/

セットアップ・デプロイ補助スクリプト群。Lambda 本体 (`src/read_later_digest/`) からは参照されない、開発者の手元で叩くツール。

| スクリプト | 目的 | いつ使うか |
|---|---|---|
| [`create-notion-db.py`](#create-notion-dbpy) | Notion DB を所定のスキーマで新規作成 | 初回セットアップ時に1回だけ |
| [`gen-env.py`](#gen-envpy) | `samconfig.toml` から `.env` を生成 | `samconfig.toml` を編集するたび |
| [`sync-requirements.py`](#sync-requirementspy) | `uv.lock` から `src/requirements.txt` を再生成 | `pyproject.toml` / `uv.lock` を変更したとき(`deploy.sh` が自動実行する) |
| [`deploy.sh`](#deploysh) | `sam build` + `sam deploy` を一括実行 | デプロイするとき |

---

## `create-notion-db.py`

`docs/functional-design.md` のスキーマ通りの Notion DB を新規作成する使い切りスクリプト。`Name` / `URL` / `Status` / `Type` / `Priority` / `AddedAt` / `Age` の 7 プロパティを持つ DB を、指定した親ページの下に作る。

### 前提

1. Notion で **Internal Integration を作成** し、Internal Integration Token (`ntn_xxx` または `secret_xxx`) を控える。
   <https://www.notion.so/my-integrations>
2. DB を配置したい **親ページ** を Notion で開き、右上「…」→ **Connections** → 上で作った Integration を招待。これをやらないと API から親ページが見えず `Could not find page` で失敗する。
3. 親ページ URL の末尾 32 桁(ハイフンあり/なしどちらでも可)が `--parent-page-id` の値。

### 使い方

```bash
# 送信予定の payload を確認(API は叩かない)
uv run python scripts/create-notion-db.py \
    --token <NOTION_TOKEN> \
    --parent-page-id <PAGE_ID> \
    --dry-run

# 実行
uv run python scripts/create-notion-db.py \
    --token <NOTION_TOKEN> \
    --parent-page-id <PAGE_ID>
```

`--token` を省略すると環境変数 `NOTION_TOKEN` を参照する。`--title` でタイトルを変更可(default: `Read Later`)。

### 出力

成功時、stdout に以下を出力:

```
database_id=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
data_source_id=yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy
```

`database_id` をそのまま `samconfig.toml` の `NotionDbId=...` に貼る。`data_source_id` は Notion API 3.x の参照用(本体 adapter が `databases.retrieve` で内部解決するため、`samconfig.toml` には不要)。

### 既知の制限

- 列の表示順は Notion 側の都合(タイトル先頭、以降アルファベット順)で決まり、API では制御できない。**気になる場合は作成後に Notion UI で 1 回ドラッグして並べ替える**。要約処理には影響しない。
- 冪等ではない。同じコマンドを再実行すると同名 DB が複数できる。失敗時は Notion 側で削除してから再実行。
- スキーマはスクリプト内ハードコード。本体 (`src/read_later_digest/adapters/notion_repository.py`) とは独立しているため、本体側の `NOTION_TYPE_VALUES` 等を env で変えても本スクリプトの出力は変わらない。

---

## `gen-env.py`

`samconfig.toml` の `parameter_overrides`(Lambda Parameter, PascalCase)と `[local]` セクション(ローカル限定 env, UPPER_SNAKE)をマージして `.env` を生成する。

```bash
uv run python scripts/gen-env.py
```

`samconfig.toml` の値を変えたら必ず再実行する。`.env` は **AUTO-GENERATED** なので直接編集しない。`PARAM_TO_ENV` マッピングは `template.yaml` の `Environment.Variables` ブロックと一致させる。

---

## `sync-requirements.py`

`pyproject.toml` + `uv.lock` を真実の出所として、SAM の Python pip builder が読む `src/requirements.txt` を pinned-no-hashes 形式で再生成する。

```bash
uv run python scripts/sync-requirements.py
```

`deploy.sh` が内部で呼ぶので通常は手動実行不要。CI 等で `sam build` を直接叩く場合のみ事前に実行する。

---

## `deploy.sh`

`sam build` + `sam deploy` のワンショットラッパー。前提チェック、`requirements.txt` 同期、WSL 環境で Windows 側 pyenv が優先されないよう Linux `python3.13` を PATH 先頭に挿入する処理込み。

```bash
scripts/deploy.sh                       # 確認プロンプトあり
scripts/deploy.sh --no-confirm-changeset # 自動承認
```

追加引数はそのまま `sam deploy` に転送される。再実行は冪等。
