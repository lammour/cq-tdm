"""The history of controls is the home view: available without loading an image."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pathlib import Path  # noqa: E402

from cq_tdm.core.app_config import AppConfig  # noqa: E402
from cq_tdm.core.device_database import DeviceConfig, DeviceDatabase  # noqa: E402
from cq_tdm.core.qc_history import QCRun  # noqa: E402
from cq_tdm.gui import main_window as mw  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Application settings and database kept inside tmp_path."""
    cfg = AppConfig(device_database_path=str(tmp_path / "devices.json"))
    monkeypatch.setattr(mw, "get_app_config", lambda: cfg)
    monkeypatch.setattr(mw, "save_app_config", lambda: None)
    return cfg


def make_device(db: DeviceDatabase, name: str, runs: int) -> DeviceConfig:
    device = DeviceConfig.from_dicom("ACME", name, "ST", name)
    device.runs = [QCRun(run_date=f"2026-0{i + 1}-01", series_uid=f"{name}-{i}") for i in range(runs)]
    db.save_device(device)
    return device


def test_single_installation_is_selected_at_startup(qapp, config):
    only = make_device(DeviceDatabase(Path(config.device_database_path)), "CT1", runs=2)
    window = mw.MainWindow()
    assert window._current_device.device_id == only.device_id
    assert window._results_tabs.currentWidget() is window.history_panel
    assert window.history_panel._table.rowCount() == 2
    assert config.last_device_id == only.device_id


def test_last_used_installation_is_reselected(qapp, config):
    db = DeviceDatabase(Path(config.device_database_path))
    make_device(db, "CT1", runs=1)
    second = make_device(db, "CT2", runs=3)
    config.last_device_id = second.device_id
    window = mw.MainWindow()
    assert window._current_device.device_id == second.device_id
    assert window.history_panel._table.rowCount() == 3
    assert window._device_combo.currentData() == second.device_id

    # Picking another installation in the selector is remembered for next time
    other_index = window._device_combo.findData(db.get_all_devices()[0].device_id)
    window._device_combo.setCurrentIndex(other_index)
    assert config.last_device_id == window._current_device.device_id != second.device_id


def test_nothing_selected_says_what_to_do(qapp, config):
    db = DeviceDatabase(Path(config.device_database_path))
    make_device(db, "CT1", runs=1)
    make_device(db, "CT2", runs=1)
    window = mw.MainWindow()  # two installations, none remembered
    assert window._current_device is None
    assert window.history_panel._summary.text().startswith("Sélectionnez une installation")
    assert window._results_tabs.currentWidget() is window.history_panel


def test_forgotten_installation_falls_back(qapp, config):
    db = DeviceDatabase(Path(config.device_database_path))
    only = make_device(db, "CT1", runs=1)
    config.last_device_id = "deleted-device"
    window = mw.MainWindow()
    assert window._current_device.device_id == only.device_id
