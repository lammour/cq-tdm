"""Main entry point for CQ TDM application."""

import os
import sys

# Suppress Qt image IO warnings (e.g., ICC profile warnings for PNG files)
os.environ["QT_LOGGING_RULES"] = "qt.gui.imageio=false"

from PySide6.QtWidgets import QApplication

from cq_tdm.gui.main_window import MainWindow


def main():
    """Launch the CQ TDM application."""
    app = QApplication(sys.argv)
    app.setApplicationName("CQ TDM")
    app.setApplicationVersion("0.1.0")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
