"""Main entry point for CQ TDM application."""

import os
import sys
from pathlib import Path

# Suppress Qt image IO warnings (e.g., ICC profile warnings for PNG files)
os.environ["QT_LOGGING_RULES"] = "qt.gui.imageio=false"

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor, QIcon
from PySide6.QtWidgets import QApplication

from cq_tdm.gui.main_window import MainWindow


def _get_icon_path() -> Path | None:
    """Get the path to the application icon."""
    # Try to find icon in package assets
    package_dir = Path(__file__).parent
    icon_candidates = [
        package_dir / "assets" / "icon.png",
        package_dir / "assets" / "icon.ico",
    ]
    for icon_path in icon_candidates:
        if icon_path.exists():
            return icon_path
    return None


def _create_dark_palette() -> QPalette:
    """Create a fixed dark color palette."""
    palette = QPalette()

    # Base colors
    dark = QColor(45, 45, 45)
    darker = QColor(30, 30, 30)
    light = QColor(220, 220, 220)
    highlight = QColor(42, 130, 218)
    disabled = QColor(127, 127, 127)

    # Window and base
    palette.setColor(QPalette.ColorRole.Window, dark)
    palette.setColor(QPalette.ColorRole.WindowText, light)
    palette.setColor(QPalette.ColorRole.Base, darker)
    palette.setColor(QPalette.ColorRole.AlternateBase, dark)
    palette.setColor(QPalette.ColorRole.ToolTipBase, dark)
    palette.setColor(QPalette.ColorRole.ToolTipText, light)

    # Text
    palette.setColor(QPalette.ColorRole.Text, light)
    palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.PlaceholderText, disabled)

    # Buttons
    palette.setColor(QPalette.ColorRole.Button, dark)
    palette.setColor(QPalette.ColorRole.ButtonText, light)

    # Highlights
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)

    # Links
    palette.setColor(QPalette.ColorRole.Link, highlight)
    palette.setColor(QPalette.ColorRole.LinkVisited, QColor(180, 100, 220))

    # Disabled state
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)

    return palette


def main():
    """Launch the CQ TDM application."""
    if "--version" in sys.argv:
        from cq_tdm import __version__
        print(f"CQ TDM {__version__}")
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setApplicationName("CQ TDM")
    from cq_tdm import __version__
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")

    # Determine theme: --light flag overrides config
    from cq_tdm.core.app_config import get_app_config
    if "--light" in sys.argv:
        sys.argv.remove("--light")
        theme = "light"
    else:
        theme = get_app_config().theme

    if theme == "dark":
        app.setPalette(_create_dark_palette())

    # Set application icon
    icon_path = _get_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(str(icon_path)))

    # Parse --size WxH (e.g. --size 1360x880)
    window_size = None
    args = app.arguments()
    for i, arg in enumerate(args):
        if arg == "--size" and i + 1 < len(args):
            try:
                w, h = args[i + 1].split("x")
                window_size = (int(w), int(h))
            except ValueError:
                pass
            break

    window = MainWindow()
    if window_size:
        window.resize(*window_size)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
