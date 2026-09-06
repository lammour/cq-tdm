"""Main entry point for CQ TDM application."""

import os
import sys
from pathlib import Path

# Suppress Qt image IO warnings (e.g., ICC profile warnings for PNG files)
os.environ["QT_LOGGING_RULES"] = "qt.gui.imageio=false"

if sys.platform.startswith("linux"):
    # Load matplotlib's FreeType binding before Qt. When Qt's bundled libfreetype is
    # loaded first, matplotlib text rendering fails with "FT_Render_Glyph ... raster
    # overflow" and the NPS plot / PDF figures cannot be produced (seen with
    # PySide6 6.11 + matplotlib 3.11). Costs ~0.2 s at startup on Linux only.
    try:
        import matplotlib.ft2font  # noqa: F401
    except Exception:
        pass

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


def _check_dependencies() -> int:
    """Import every runtime dependency and lazily-loaded module; return exit code.

    Used by the CI smoke test on the frozen executables so that a package
    missing from the bundle is caught at build time instead of by users.
    """
    import importlib

    modules = [
        "numpy",
        "scipy.ndimage",
        "pydicom",
        "PIL.Image",
        "matplotlib.pyplot",
        "matplotlib.backends.backend_agg",
        "matplotlib.patches",
        "reportlab.platypus",
        "reportlab.pdfbase.ttfonts",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "cq_tdm.core.dicom_loader",
        "cq_tdm.core.water_phantom",
        "cq_tdm.core.nps",
        "cq_tdm.reports.pdf_report",
    ]
    failures = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - report every failure
            failures.append(f"{name}: {exc}")
    if failures:
        print("Missing or broken dependencies:")
        for line in failures:
            print(f"  {line}")
        return 1
    print(f"All {len(modules)} runtime modules imported successfully")
    return 0


def main():
    """Launch the CQ TDM application."""
    if "--version" in sys.argv:
        from cq_tdm import __version__
        print(f"CQ TDM {__version__}")
        sys.exit(0)

    if "--check-deps" in sys.argv:
        sys.exit(_check_dependencies())

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
