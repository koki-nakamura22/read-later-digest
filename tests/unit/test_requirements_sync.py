"""Drift guard: src/requirements.txt must stay in sync with uv.lock.

`sam build` packages Lambda dependencies from `src/requirements.txt`. If a
contributor adds/upgrades a dep in pyproject.toml + uv.lock but forgets to
re-run `scripts/sync-requirements.py`, the Lambda zip would silently miss
the new package and crash with ImportError at runtime. This test fails the
build before that happens.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS_PATH = ROOT / "src" / "requirements.txt"


def _has_uv() -> bool:
    return shutil.which("uv") is not None


@pytest.mark.skipif(not _has_uv(), reason="uv binary not available in this environment")
def test_requirements_txt_matches_uv_export() -> None:
    proc = subprocess.run(
        ["uv", "export", "--no-hashes", "--no-dev", "--no-emit-project", "--frozen"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = proc.stdout
    actual = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    expected_norm = _normalize(expected)
    actual_norm = _normalize(actual)
    assert actual_norm == expected_norm, (
        "src/requirements.txt is stale. Run: uv run python scripts/sync-requirements.py"
    )


def _normalize(text: str) -> str:
    """Strip the `#    uv export ...` header line (the `-o <abs path>` form
    differs between dev and CI) and any indented `# via ...` annotations
    (newer uv versions emit them, older ones don't) so the comparison is
    stable across uv versions and invocation styles."""
    return "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith("#    uv export") and not line.startswith((" ", "\t"))
    )
