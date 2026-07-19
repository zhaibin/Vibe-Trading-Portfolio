"""Package-relative access to the built portfolio web application."""

from pathlib import Path


def web_dist_path() -> Path:
    """Return the installed package's frontend distribution directory."""
    return Path(__file__).resolve().parent / "dist"


__all__ = ["web_dist_path"]
