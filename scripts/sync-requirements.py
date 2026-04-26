#!/usr/bin/env python3
"""Regenerate `src/requirements.txt` from `uv.lock` for SAM Lambda packaging.

`sam build` invokes pip with `requirements.txt` to install runtime dependencies
into the Lambda zip. The project's authoritative dependency source is
`pyproject.toml` + `uv.lock`, so we materialize a pinned, no-hashes
requirements.txt for the Python pip builder.

Run before `sam build` whenever pyproject.toml / uv.lock changes:
    uv run python scripts/sync-requirements.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "requirements.txt"


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("error: 'uv' not found on PATH", file=sys.stderr)
        return 1
    proc = subprocess.run(
        [
            uv,
            "export",
            "--no-hashes",
            "--no-dev",
            "--no-emit-project",
            "--frozen",
            "-o",
            str(TARGET),
        ],
        cwd=ROOT,
        check=False,
    )
    if proc.returncode != 0:
        return proc.returncode
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
