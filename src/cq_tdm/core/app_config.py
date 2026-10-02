"""Application configuration for global settings."""

import json
from pathlib import Path
from dataclasses import dataclass, asdict, fields

from .utils import atomic_write_json

from PySide6.QtCore import QStandardPaths


@dataclass
class AppConfig:
    """Global application settings."""

    # Report settings
    report_logo_path: str = ""
    report_logo_scale: float = 0.4  # Logo scale (10-100% of page width)
    # Append the "Historique des contrôles" section (table + trend charts) to the
    # PDF. Off by default: the report of one control stands on its own, and past
    # controls are not always meant to be handed over with it.
    report_include_history: bool = False

    # Database settings
    device_database_path: str = ""  # Custom path to devices.json (empty = default)
    # Installation selected last, reselected at startup so its history of
    # controls is on screen without loading an image
    last_device_id: str = ""

    # Names printed in the validation block of the last report, offered again
    last_performed_by: str = ""
    last_validated_by: str = ""

    # CSV export of the history: "fr" (";" between columns, decimal comma, what a
    # French spreadsheet opens as numbers) or "en" ("," and decimal point)
    csv_format: str = "fr"

    # UI settings
    theme: str = "dark"  # "dark" or "light"

    @classmethod
    def config_dir(cls) -> Path:
        """Get the platform-specific config directory.

        Returns:
            - Windows: %APPDATA%/cq_tdm/
            - Linux: ~/.config/cq_tdm/
        """
        base_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)
        config_dir = Path(base_path) / "cq_tdm"
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # Read-only location: callers handle the failing open()
        return config_dir

    @classmethod
    def config_path(cls) -> Path:
        """Get the path to the config file."""
        return cls.config_dir() / "settings.json"

    @classmethod
    def load(cls) -> "AppConfig":
        """Load config from file, creating it with defaults if missing.

        An unreadable file is renamed to settings.json.bak before defaults are
        written, so the user's custom database path and logo settings can be
        recovered by hand.
        """
        config_path = cls.config_path()
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls._from_dict(data)
            except (OSError, ValueError, TypeError, AttributeError):
                try:
                    config_path.replace(config_path.with_suffix(".json.bak"))
                except OSError:
                    pass

        # Create new config with defaults and save it
        config = cls()
        try:
            config.save()
        except OSError:
            pass  # Read-only config dir: run with defaults, do not crash at startup
        return config

    @classmethod
    def _from_dict(cls, data: dict) -> "AppConfig":
        """Settings from a stored dict; a value of the wrong type falls back to its default.

        A hand-edited file ("report_logo_scale": "40 %") must not surface later
        as a TypeError in the dialog that uses the value.
        """
        config = cls()
        for f in fields(cls):
            if f.name not in data:
                continue
            value, default = data[f.name], getattr(config, f.name)
            if isinstance(default, bool):
                valid = isinstance(value, bool)
            elif isinstance(default, float):
                valid = isinstance(value, (int, float)) and not isinstance(value, bool)
                value = float(value) if valid else value
            else:
                valid = isinstance(value, type(default))
            if valid:
                setattr(config, f.name, value)
        if config.theme not in ("dark", "light"):
            config.theme = "dark"
        if config.csv_format not in ("fr", "en"):
            config.csv_format = "fr"
        config.report_logo_scale = min(1.0, max(0.1, config.report_logo_scale))
        return config

    def save(self):
        """Save config to file (atomically: an interrupted write keeps the previous file)."""
        atomic_write_json(self.config_path(), asdict(self))


# Singleton instance
_app_config: AppConfig | None = None


def get_app_config() -> AppConfig:
    """Get the global app config (singleton)."""
    global _app_config
    if _app_config is None:
        _app_config = AppConfig.load()
    return _app_config


def save_app_config():
    """Save the global app config."""
    global _app_config
    if _app_config is not None:
        _app_config.save()
