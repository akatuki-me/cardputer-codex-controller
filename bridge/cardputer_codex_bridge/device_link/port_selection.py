from __future__ import annotations

from pathlib import Path


class PortSelectionError(ValueError):
    """Machine-localなport選択が不正。"""


def resolve_port(*, explicit_port: str | None, handle_file: Path | None) -> str:
    """明示値またはGit管理外のlocal handleからportを解決し、値は表示しない。"""
    if (explicit_port is None) == (handle_file is None):
        raise PortSelectionError("exactly one port source is required")
    if handle_file is not None:
        try:
            raw = handle_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PortSelectionError("local port handle could not be read") from exc
        lines = raw.splitlines()
        if len(lines) != 1:
            raise PortSelectionError("local port handle must contain one line")
        selected = lines[0].strip()
    else:
        assert explicit_port is not None
        selected = explicit_port.strip()
    if not selected or len(selected) > 512 or "\x00" in selected:
        raise PortSelectionError("selected port is invalid")
    return selected


__all__ = ["PortSelectionError", "resolve_port"]
