"""Utility functions for CQ TDM."""


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
