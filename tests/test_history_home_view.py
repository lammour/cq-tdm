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


def test_export_asks_before_an_incomplete_control(qapp, config, monkeypatch):
    """Exporting without artifact inspection or references must not go unnoticed."""
    from types import SimpleNamespace

    make_device(DeviceDatabase(Path(config.device_database_path)), "CT1", runs=1)
    window = mw.MainWindow()
    window._current_results = SimpleNamespace(water_ct_number=0.5, uniformity=1.0, phantom_detected=True)
    window._nps_results = SimpleNamespace(mean_frequency=0.3, noise=3.0, phantom_detected=True)
    window._edit_ref_noise.setText("3,0")
    window._edit_ref_nps_freq.setText("0,300")
    window._artifact_result = False

    shown = []
    monkeypatch.setattr(mw.QMessageBox, "exec", lambda box: shown.append(box.text()))
    assert window._confirm_incomplete_export() is True and shown == []

    window._artifact_result = None
    window._edit_ref_noise.setText("")
    assert window._confirm_incomplete_export() is False  # nothing clicked: not exported
    assert "inspection visuelle des artéfacts non réalisée" in shown[0]
    assert "stabilité du bruit non évaluée" in shown[0]
    assert "CONTRÔLE INCOMPLET" in shown[0]


def test_forgotten_installation_falls_back(qapp, config):
    db = DeviceDatabase(Path(config.device_database_path))
    only = make_device(db, "CT1", runs=1)
    config.last_device_id = "deleted-device"
    window = mw.MainWindow()
    assert window._current_device.device_id == only.device_id


def test_controls_recorded_on_another_workstation_appear(qapp, config):
    """The database is re-read when it changed on disk, without restarting."""
    path = Path(config.device_database_path)
    only = make_device(DeviceDatabase(path), "CT1", runs=1)
    window = mw.MainWindow()
    assert window.history_panel._table.rowCount() == 1
    shown_device = window._current_device

    elsewhere = DeviceDatabase(path)
    elsewhere.add_run(only.device_id, QCRun(run_date="2026-08-01", series_uid="autre-poste"))
    elsewhere.save_device(DeviceConfig.from_dicom("ACME", "CT2", "ST", "CT2"))

    window._refresh_database(force=True)
    assert window._current_device is shown_device  # same object, updated in place
    assert window.history_panel._table.rowCount() == 2
    assert window._device_combo.count() == 3  # placeholder + the two installations
    assert "mise à jour depuis un autre poste" in window.statusbar.currentMessage()

    # Nothing changed since: no reload, no message
    window.statusbar.clearMessage()
    window._refresh_database(force=True)
    assert window.statusbar.currentMessage() == ""


def test_installation_deleted_on_another_workstation_is_dropped(qapp, config):
    path = Path(config.device_database_path)
    db = DeviceDatabase(path)
    first = make_device(db, "CT1", runs=1)
    second = make_device(db, "CT2", runs=2)
    config.last_device_id = first.device_id
    window = mw.MainWindow()
    assert window._current_device.device_id == first.device_id

    DeviceDatabase(path).delete_device(first.device_id)
    window._refresh_database(force=True)
    assert window._current_device.device_id == second.device_id  # the only one left
    assert window.history_panel._table.rowCount() == 2


def test_unreachable_database_folder_is_announced(qapp, config, tmp_path, monkeypatch):
    """A network share that is not mounted must not look like an empty database."""
    config.device_database_path = str(tmp_path / "partage" / "devices.json")
    shown = []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda parent, title, text: shown.append(text))
    window = mw.MainWindow()
    mw.warn_database_load(window, window._device_db)
    assert "est inaccessible" in shown[0] and "partage réseau" in shown[0]
    assert window._device_db.read_only

    # The share comes back: its installations show up at the next check
    (tmp_path / "partage").mkdir()
    make_device(DeviceDatabase(Path(config.device_database_path)), "CT1", runs=3)
    window._refresh_database(force=True)
    assert window._current_device is not None
    assert window.history_panel._table.rowCount() == 3
