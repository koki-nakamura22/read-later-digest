# read-later-digest

Notion DB に溜まる「後で読む」記事を、LLM で要約して日次でメール通知し、Notion の Status を自動更新するツール。

詳細は `docs/` 配下のドキュメントを参照。

## 開発

### セットアップ

```bash
uv venv
uv pip install -e ".[dev]"
```

### テスト

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src
```

### ローカル実行

```bash
# 通常実行(取得 → 要約 → ダイジェスト送信 → Notion 更新)
uv run python -m read_later_digest.run

# ドライラン(メール送信 / Notion 書き戻しを行わない)
uv run python -m read_later_digest.run --dry-run
```

必要な環境変数: `NOTION_DB_ID`, `NOTION_TOKEN`, `ANTHROPIC_API_KEY`, `MAIL_FROM`, `MAIL_TO`(任意で `LLM_CONCURRENCY`, `FETCH_TIMEOUT_SEC` ほか — `src/read_later_digest/config.py` を参照)。

## デプロイ (AWS Lambda + EventBridge)

`template.yaml` で Lambda 関数 / EventBridge 日次スケジュール / IAM ロール / CloudWatch Logs を宣言している。

```bash
# 初回(対話形式でパラメータと S3 バケットを設定)
sam build
sam deploy --guided \
  --parameter-overrides \
    NotionDbId=xxx MailFrom=digest@example.com MailTo=me@example.com

# 2 回目以降
sam deploy
```

主なパラメータ:

| パラメータ | 既定値 | 用途 |
|---|---|---|
| `ScheduleExpression` | `cron(0 22 * * ? *)` (= 07:00 JST) | EventBridge 日次起動 |
| `LambdaTimeoutSeconds` | 600 | Lambda タイムアウト(最大 900) |
| `LambdaMemorySizeMb` | 512 | Lambda メモリ |
| `LogRetentionDays` | 30 | CloudWatch Logs 保持期間 |

### 一時停止 / 再開

EventBridge スケジュールを `Enabled: false` に切り替えるか、コンソール上で対象スケジュールを Disable する。コードを残したまま起動だけ止める運用を推奨。
