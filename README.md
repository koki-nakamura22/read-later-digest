# read-later-digest

Notion DB に溜まる「後で読む」記事を、LLM で要約して日次でメール通知し、Notion の Status を自動更新するツール。

詳細は `docs/` 配下のドキュメントを参照。

## 開発

### セットアップ

```bash
uv venv
uv pip install -e ".[dev]"

# samconfig.toml を作成し、自身の環境に合わせて値を編集
cp samconfig.toml.tmpl samconfig.toml

# samconfig.toml から .env を生成
uv run python scripts/gen-env.py
```

`samconfig.toml` は環境変数の単一の出所として、ローカル実行 (`uv run`) と デプロイ (`sam deploy`) の両方を駆動する。詳細は [環境変数](#環境変数) を参照。

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

`.env` は `samconfig.toml` から自動生成されるため、直接編集しない。値を変更したい場合は `samconfig.toml` を編集して `uv run python scripts/gen-env.py` を再実行する。

## 環境変数

`samconfig.toml`(git 管理外、`samconfig.toml.tmpl` から `cp` で作成)が真実の出所。以下の2系統から成る:

1. `[default.deploy.parameters].parameter_overrides` — `template.yaml` の Parameters (PascalCase) と対応。`sam deploy` がそのまま使い、`scripts/gen-env.py` が `NotionDbId` → `NOTION_DB_ID` のように変換して `.env` にも展開する。**機密 (`NotionToken` / `AnthropicApiKey` / `SlackWebhookUrl`) もここに直書きする**。`template.yaml` 側で `NoEcho: true` 指定のため CloudFormation コンソールや `describe-stacks` 出力ではマスクされる。
2. `[local]` — Lambda には流れない、ローカル限定の env (UPPER_SNAKE)。Lambda runtime がビルトインで提供する `AWS_REGION` などのみ。

全 env のデフォルト値・型は `src/read_later_digest/config.py` を参照。

> **将来検討**: リポジトリ共有 / CI 導入 / prod 環境分離が発生したタイミングで、機密の保管先を AWS Secrets Manager に移行することを検討する。それまでは個人利用前提で `samconfig.toml`(.gitignore 済み)+ `NoEcho` パラメータの組み合わせで運用する。

## デプロイ (AWS Lambda + EventBridge)

`template.yaml` で Lambda 関数 / EventBridge 日次スケジュール / IAM ロール / CloudWatch Logs を宣言している。`samconfig.toml` の `parameter_overrides` を読むため、コマンドラインでの override は不要。

```bash
# pyproject.toml / uv.lock の変更を src/requirements.txt に反映 (sam build はこれを参照)
uv run python scripts/sync-requirements.py

sam build
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
