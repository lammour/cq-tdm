"""Application configuration for global settings."""

import json
from pathlib import Path
from dataclasses import dataclass, asdict

from PySide6.QtCore import QStandardPaths


@dataclass
class AppConfig:
    """Global application settings."""

    # Report settings
    report_logo_path: str = ""
    report_logo_scale: float = 0.4  # Logo scale (10-100% of page width)

    # Database settings
    device_database_path: str = ""  # Custom path to devices.json (empty = default)

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
        config_dir.mkdir(parents=True, exist_ok=True)
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
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except (OSError, ValueError, TypeError):
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

    def save(self):
        """Save config to file."""
        config_path = self.config_path()
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)


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
