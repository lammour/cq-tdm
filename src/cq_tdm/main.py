"""Main entry point for CQ TDM application."""

import logging
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
        "matplotlib.dates",
        "reportlab.platypus",
        "reportlab.pdfbase.ttfonts",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "cq_tdm.core.dicom_loader",
        "cq_tdm.core.dicom_locator",
        "cq_tdm.core.water_phantom",
        "cq_tdm.core.nps",
        "cq_tdm.core.qc_history",
        "cq_tdm.core.trend_chart",
        "cq_tdm.gui.history_panel",
        "cq_tdm.gui.image_viewer",
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
    # Not fatal: the application's own dialogs carry French buttons anyway
    if _french_qt_translation() is None:
        print("Warning: Qt French translation (qtbase_fr) not found in this build")
    return 0


def _french_qt_translation():
    """The loaded Qt base translation for French, or None when the build has none."""
    from PySide6.QtCore import QLibraryInfo, QTranslator

    translator = QTranslator()
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    return translator if translator.load("qtbase_fr", path) else None


def _install_french(app: QApplication) -> None:
    """Make Qt's own texts French: standard buttons, file dialogs, calendars, context menus.

    The interface is French only, whatever the language of the operating system.
    """
    from PySide6.QtCore import QLocale

    QLocale.setDefault(QLocale(QLocale.Language.French, QLocale.Country.France))
    translator = _french_qt_translation()
    if translator is not None:
        translator.setParent(app)  # keep it alive as long as the application
        app.installTranslator(translator)


def _setup_logging() -> None:
    """Send the application log to cq_tdm.log, next to the settings file.

    Errors that are handled on screen (an analysis that fails, a figure that
    cannot be drawn for the report) leave a trace there for support.
    """
    from logging.handlers import RotatingFileHandler

    try:
        handler = RotatingFileHandler(_crash_log_path(), maxBytes=1_000_000, backupCount=1,
                                      encoding="utf-8", delay=True)
    except OSError:
        return  # read-only profile: run without a log file
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("cq_tdm")
    root.setLevel(logging.DEBUG if "--verbose" in sys.argv else logging.INFO)
    root.addHandler(handler)


def _crash_log_path() -> Path:
    """Path of the crash log, next to settings.json in the user config directory."""
    from cq_tdm.core.app_config import AppConfig
    return AppConfig.config_dir() / "cq_tdm.log"


def _write_crash_log(text: str) -> Path | None:
    """Append text to the crash log; return its path, or None if it cannot be written."""
    try:
        log_path = _crash_log_path()
        # Keep the log bounded: roll over once it exceeds 1 MB
        if log_path.exists() and log_path.stat().st_size > 1_000_000:
            log_path.replace(log_path.with_suffix(".log.1"))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text)
        return log_path
    except OSError:
        return None


def _handle_uncaught_exception(exc_type, exc_value, exc_tb):
    """Log an unhandled exception and show it to the user.

    In the windowed executable stdout/stderr do not exist, so without this an
    exception raised inside a Qt slot vanishes and the user only sees that
    "nothing happens". PySide6 routes such exceptions through sys.excepthook.
    """
    import traceback
    from datetime import datetime

    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    from cq_tdm import __version__
    details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = _write_crash_log(
        f"\n===== {stamp} | CQ TDM {__version__} | Python {sys.version.split()[0]}"
        f" | {sys.platform} =====\n{details}"
    )

    # Also print when a console is available (development runs)
    if sys.stderr is not None:
        sys.stderr.write(details)

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is None:
            return
        summary = f"{exc_type.__name__}: {exc_value}"
        where = (
            f"Le détail a été enregistré dans :\n{log_path}"
            if log_path else "Le détail n'a pas pu être enregistré dans un fichier journal."
        )
        box = QMessageBox(
            QMessageBox.Icon.Critical,
            "Erreur inattendue",
            "Une erreur inattendue s'est produite. L'opération en cours a été interrompue.\n\n"
            f"{summary}\n\n{where}\n\n"
            "Merci de joindre ce fichier à tout signalement de problème.",
        )
        box.setDetailedText(details)
        box.exec()
    except Exception:
        pass  # Never let the error handler itself crash the application


def main():
    """Launch the CQ TDM application."""
    if "--version" in sys.argv:
        from cq_tdm import __version__
        print(f"CQ TDM {__version__}")
        sys.exit(0)

    if "--check-deps" in sys.argv:
        sys.exit(_check_dependencies())

    sys.excepthook = _handle_uncaught_exception
    _setup_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("CQ TDM")
    from cq_tdm import __version__
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    _install_french(app)
    logging.getLogger("cq_tdm").info("CQ TDM %s started (Python %s, %s)",
                                     __version__, sys.version.split()[0], sys.platform)

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
