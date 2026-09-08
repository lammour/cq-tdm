"""Shared colors and widget styles for the GUI.

Kept in its own module so the main window and the image viewer use the same
values; the viewer cannot import from the main window, which imports it.
"""

from ..core import get_app_config


def theme_colors() -> dict:
    """Return color dict based on current theme setting."""
    is_light = get_app_config().theme == "light"
    if is_light:
        return {
            "bg": "#ffffff",
            "text": "#222222",
            "text_secondary": "#555555",
            "border": "#cccccc",
            "section_title": "#1565c0",
            "section_border": "#1565c0",
            "th": "#666666",
            "td": "#222222",
            "roi_header_bg": "#e0e0e0",
            "pending": "#999999",
            "warning_bg": "#fff3e0",
            "warning_border": "#ff9800",
            "warning_title": "#e65100",
            "warning_text": "#bf360c",
            "accent": "#1565c0",
            "accent_hover": "#1976d2",
            "accent_pressed": "#0d47a1",
        }
    else:
        return {
            "bg": "#2b2b2b",
            "text": "#e0e0e0",
            "text_secondary": "#aaaaaa",
            "border": "#444444",
            "section_title": "#4fc3f7",
            "section_border": "#4fc3f7",
            "th": "#aaaaaa",
            "td": "#ffffff",
            "roi_header_bg": "#333333",
            "pending": "#888888",
            "warning_bg": "#4a3000",
            "warning_border": "#ff9800",
            "warning_title": "#ff9800",
            "warning_text": "#ffcc80",
            "accent": "#2a82da",
            "accent_hover": "#4a9ae8",
            "accent_pressed": "#1c6bb8",
        }


def primary_button_style(padding: str = "4px 12px", font_size: str = "",
                         radius: str = "3px") -> str:
    """Stylesheet marking a button as *the* action to take now.

    A single accent fill is the only emphasis used for that meaning, so the
    welcome screen, the installation selector and the save button all read as
    the same invitation. Green, orange and amber stay free for their own
    meanings (conformity verdicts, warnings, highlighted values).
    """
    c = theme_colors()
    size = f" font-size: {font_size};" if font_size else ""
    return (
        "QPushButton {"
        f" background-color: {c['accent']}; color: #ffffff; font-weight: bold;"
        f" border: none; border-radius: {radius}; padding: {padding};{size} }}"
        f"QPushButton:hover {{ background-color: {c['accent_hover']}; }}"
        f"QPushButton:pressed {{ background-color: {c['accent_pressed']}; }}"
        f"QPushButton:disabled {{ background-color: {c['border']};"
        f" color: {c['pending']}; }}"
    )


def tooltip_style() -> str:
    """Tooltip rule to append to any widget that carries its own stylesheet.

    A tooltip inherits the stylesheet of the widget it belongs to. A rule such
    as `background-color` therefore repaints the tooltip too, while its text
    colour falls back to the stylesheet default (black) instead of the palette's
    ToolTipText: black on dark grey in the dark theme. Stating both colours
    fixes it.
    """
    c = theme_colors()
    return (f"QToolTip {{ color: {c['text']}; background-color: {c['bg']};"
            f" border: 1px solid {c['border']}; padding: 3px; }}")
