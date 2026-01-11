"""Main entry point for CQ TDM application."""

import os
import sys

# Suppress Qt image IO warnings (e.g., ICC profile warnings for PNG files)
os.environ["QT_LOGGING_RULES"] = "qt.gui.imageio=false"

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication

from cq_tdm.gui.main_window import MainWindow


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
    app = QApplication(sys.argv)
    app.setApplicationName("CQ TDM")
    app.setApplicationVersion("0.2.0")
    app.setStyle("Fusion")
    app.setPalette(_create_dark_palette())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
