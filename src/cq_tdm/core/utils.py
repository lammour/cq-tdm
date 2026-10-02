"""Utility functions for CQ TDM."""

import json
import os
import tempfile
import time
from pathlib import Path


def atomic_write_json(path: Path, data) -> None:
    """Write `data` as JSON to `path` without ever leaving a truncated file.

    The content goes to a temporary file in the same folder, which then
    replaces the target in one step: a crash or a full disk mid-write leaves
    the previous file intact. The temporary name is unique, so two writers
    cannot truncate each other's file.
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # On Windows the swap is refused while another process has the target
        # open, e.g. another workstation reading a shared database: a read
        # lasts milliseconds, so try again briefly before giving up
        for attempt in range(10):
            try:
                os.replace(tmp_name, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def format_fr(value: float, decimals: int = 1, sign: bool = False) -> str:
    """Format a number with French decimal separator (comma).

    Args:
        value: The number to format.
        decimals: Number of decimal places.
        sign: If True, always show sign (+ or -).

    Returns:
        Formatted string with comma as decimal separator.
    """
    if sign:
        formatted = f"{value:+.{decimals}f}"
    else:
        formatted = f"{value:.{decimals}f}"
    return formatted.replace(".", ",")


def parse_float_fr(text: str) -> float | None:
    """Parse a float accepting both dot and comma as decimal separator.

    Args:
        text: String to parse.

    Returns:
        Parsed float, or None if parsing fails.
    """
    text = text.strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None
