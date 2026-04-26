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

### 前提

- AWS CLI / SAM CLI / uv がインストール済み
- `aws sts get-caller-identity` が成功する状態(認証情報が設定済み)
- `samconfig.toml` を作成済み(`cp samconfig.toml.tmpl samconfig.toml` 後、自分の値に編集)

### 一発デプロイ(推奨)

`scripts/deploy.sh` が以下を 1 コマンドにまとめてある:

1. 前提チェック(`samconfig.toml` / 必要 CLI / AWS 認証)
2. `src/requirements.txt` を `uv.lock` から再生成(`sam build` がこれを使う)
3. Linux 側の `python3.13` を解決して PATH に通す(WSL で Windows pyenv shims が混入する環境を吸収)
4. `sam build`
5. `sam deploy`(追加引数はそのまま `sam deploy` に転送される)

```bash
# 対話あり(変更セットを確認してから適用)
scripts/deploy.sh

# 非対話(CI など。samconfig.toml の confirm_changeset を上書き)
scripts/deploy.sh --no-confirm-changeset
```

### 動作確認(任意)

スケジュールを待たずに 1 回実行する場合:

```bash
aws lambda invoke --function-name read-later-digest --region ap-northeast-1 \
    --cli-binary-format raw-in-base64-out --payload '{}' /tmp/out.json && cat /tmp/out.json

# ログ追跡
sam logs --stack-name read-later-digest --tail
```

### 個別コマンドで実行する場合

スクリプトを使わずに手動で進める場合は同等のコマンドを順に:

```bash
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
| `NotifyChannels` | `mail` | 通知 channel(`mail` / `slack` カンマ区切り) |
| `NotifyGranularityMail` | `digest` | mail channel の通知粒度。詳細は [通知粒度の選択](#通知粒度の選択) |
| `NotifyGranularitySlack` | `digest` | Slack channel の通知粒度。mail とは独立に設定可。 |

### 通知粒度の選択

通知粒度は **channel ごとに独立して** 設定できる。例えば「mail はまとめて読みたいので `digest` のまま、Slack はスレッド分割したいので `per_article`」のような混在運用が可能。

| 値 | 挙動 |
|---|---|
| `digest`(既定) | 成功・失敗をまとめた 1 通を送る(従来挙動)。設定変更不要で従来動作を維持。 |
| `per_article` | 成功記事ごとに 1 通 + 失敗があれば末尾に集約サマリ 1 通。Slack でスレッド分割や個別記事への絵文字リアクションを使う運用向け。 |

設定例:

| ユースケース | `NotifyGranularityMail` | `NotifyGranularitySlack` |
|---|---|---|
| 従来挙動を維持 | `digest` | `digest` |
| Slack だけ記事ごとに分けたい | `digest` | `per_article` |
| 両方とも記事ごと | `per_article` | `per_article` |

通知途中に送信が失敗するとバッチを中断する(Notion 書き戻しは実行されない)。mailer → notifier の順で送信されるため、mail が成功した後に Slack の途中で失敗したケースでも writeback は走らず、次回バッチで全件再処理(重複通知許容)となる。詳細は `docs/functional-design.md` の「通知粒度」節を参照。

ローカルで切り替える場合は `samconfig.toml` の `NotifyGranularityMail=...` / `NotifyGranularitySlack=...` を編集して `uv run python scripts/gen-env.py` を再実行する。Lambda 側は次回 `sam deploy` で反映される。

### 一時停止 / 再開

EventBridge スケジュールを `Enabled: false` に切り替えるか、コンソール上で対象スケジュールを Disable する。コードを残したまま起動だけ止める運用を推奨。
