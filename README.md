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
