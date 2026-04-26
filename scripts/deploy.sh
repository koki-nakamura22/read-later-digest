#!/usr/bin/env bash
# Build and deploy read-later-digest to AWS Lambda via SAM.
#
# Usage:
#     scripts/deploy.sh                      # interactive (confirm changeset)
#     scripts/deploy.sh --no-confirm-changeset
#
# This wraps the multi-step deploy flow into one command:
#   1. Verify prerequisites (samconfig.toml, sam / aws / uv on PATH, AWS creds)
#   2. Regenerate src/requirements.txt from uv.lock so SAM can install runtime deps
#   3. Resolve a Linux python3.13 interpreter and prepend it to PATH
#      (works around WSL setups where Windows pyenv shims would otherwise win)
#   4. sam build
#   5. sam deploy (any extra args are forwarded as-is)
#
# Re-running is safe: each step is idempotent.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '\n[deploy] %s\n' "$*"; }
die() { printf '\n[deploy] error: %s\n' "$*" >&2; exit 1; }

# ---------- 1. Preflight checks ----------

[ -f "$ROOT/samconfig.toml" ] || die "samconfig.toml not found. Run: cp samconfig.toml.tmpl samconfig.toml && edit"

for bin in uv sam aws; do
    command -v "$bin" >/dev/null 2>&1 || die "'$bin' not on PATH (install it first)"
done

aws sts get-caller-identity >/dev/null 2>&1 \
    || die "AWS credentials are not configured (run 'aws configure' or set up SSO)"

# ---------- 2. Regenerate requirements.txt ----------

log "syncing src/requirements.txt from uv.lock"
uv run python "$ROOT/scripts/sync-requirements.py"

# ---------- 3. Locate a Linux python3.13 ----------

# uv resolves the project venv's interpreter. Its bin/ dir contains the
# python3.13 symlink that SAM's PythonPipBuilder looks for via PATH.
PY_BIN="$(uv run python -c 'import sys, pathlib; print(pathlib.Path(sys.executable).resolve())')"
PY_DIR="$(dirname "$PY_BIN")"
[ -x "$PY_DIR/python3.13" ] || die "python3.13 not found in resolved venv ($PY_DIR)"

log "using python: $PY_DIR/python3.13"

# ---------- 4. Build ----------

log "sam build"
PATH="$PY_DIR:$PATH" sam build

# ---------- 5. Deploy ----------

log "sam deploy ${*:-}"
PATH="$PY_DIR:$PATH" sam deploy "$@"

log "done. invoke with:"
printf "  aws lambda invoke --function-name read-later-digest --region ap-northeast-1 \\\\\n"
printf "    --cli-binary-format raw-in-base64-out --payload '{}' /tmp/out.json && cat /tmp/out.json\n"
