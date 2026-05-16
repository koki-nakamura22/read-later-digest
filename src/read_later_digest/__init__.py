"""read-later-digest package."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("read-later-digest")
except PackageNotFoundError:
    # Fallback for environments without installed dist metadata
    # (e.g. AWS Lambda zip deploy where only source files are shipped).
    # Keep this in sync with pyproject.toml [project] version.
    __version__ = "0.3.0"

__all__ = ["__version__"]
