"""The installations window: the single place where installations are edited."""

import dataclasses
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cq_tdm.core.device_database import DeviceConfig, DeviceDatabase  # noqa: E402
from cq_tdm.gui.main_window import DeviceManagerDialog  # noqa: E402

from .test_phantom_detection import make_phantom  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path, monkeypatch):
    # The dialog records the database path in the application settings: keep
    # the real settings file out of the tests
    monkeypatch.setattr("cq_tdm.gui.main_window.save_app_config", lambda: None)
    return DeviceDatabase(tmp_path / "devices.json")


def scanner_image():
    return dataclasses.replace(make_phantom(), manufacturer="ACME", model_name="CT 9000",
                               station_name="ST1", device_serial_number="SN42")


def test_installation_is_editable_without_an_image(qapp, db):
    device = DeviceConfig.from_dicom("ACME", "CT 9000", "ST1", "SN42")
    db.save_device(device)

    dialog = DeviceManagerDialog(db, None, select_device_id=device.device_id)
    assert dialog.selected_device_id == device.device_id
    assert dialog._btn_save.isEnabled()
    assert not dialog._btn_new_from_image.isEnabled()  # nothing to create from
    assert not dialog._btn_adopt_slices.isEnabled()  # no series to take slices from

    dialog._edit_hospital.setText("CHU Exemple")
    dialog._edit_ref_noise.setText("4,52")
    dialog._edit_register["phantom_brand"].setText("PTW")
    dialog._edit_register["reconstruction_algorithm"].setText("iDose niveau 3")
    dialog._save_current_device()

    saved = DeviceDatabase(db.db_path).get_device(device.device_id)
    assert saved.hospital_name == "CHU Exemple"
    assert saved.reference_noise == 4.52
    assert saved.phantom_brand == "PTW"
    assert saved.reconstruction_algorithm == "iDose niveau 3"
    # Still selected after the save, ready for further edits
    assert dialog.selected_device_id == device.device_id


def test_new_installation_from_the_loaded_image(qapp, db):
    dialog = DeviceManagerDialog(db, None, current_image=scanner_image(), current_slices=(6, 1, 10))
    assert dialog._btn_new_from_image.isEnabled()
    dialog._create_from_image()

    devices = db.get_all_devices()
    assert len(devices) == 1
    created = devices[0]
    assert created.dicom_model_name == "CT 9000" and created.device_name == "ACME CT 9000"
    assert (created.hu_slice_index, created.nps_start_slice, created.nps_end_slice) == (6, 1, 10)
    assert dialog.selected_device_id == created.device_id
    assert "(image chargée)" in dialog._device_list.item(0).text()
    # Already created: the button must not offer a duplicate
    assert not dialog._btn_new_from_image.isEnabled()


def test_slices_are_recorded_only_for_the_loaded_scanner(qapp, db):
    other = DeviceConfig.from_dicom("OTHER", "X", "ST9", "SN9")
    db.save_device(other)
    dialog = DeviceManagerDialog(db, None, current_image=scanner_image(), current_slices=(3, 0, 9))
    dialog._create_from_image()
    assert dialog._btn_adopt_slices.isEnabled()

    dialog.current_slices = (4, 2, 11)
    dialog._adopt_current_slices()
    mine = db.get_device(dialog.selected_device_id)
    assert (mine.hu_slice_index, mine.nps_start_slice, mine.nps_end_slice) == (4, 2, 11)

    dialog._refresh_device_list(other.device_id)
    assert not dialog._btn_adopt_slices.isEnabled()


def test_references_from_the_analysis_on_screen(qapp, db, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from cq_tdm.core.roi_geometry import ROIGeometry

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    geometry = ROIGeometry.from_phantom(400, 0.5, 512, 512).frozen("2026-10-01")
    other = DeviceConfig.from_dicom("OTHER", "X", "ST9", "SN9")
    db.save_device(other)

    dialog = DeviceManagerDialog(db, None, current_image=scanner_image(), current_slices=(6, 1, 10),
                                 current_analysis=(25.1, 0.28, geometry))
    dialog._create_from_image()
    assert dialog._btn_ref_from_analysis.isEnabled()

    dialog._edit_hospital.setText("CHU Exemple")  # typed but not saved yet
    dialog._set_references_from_analysis()
    saved = DeviceDatabase(db.db_path).get_device(dialog.selected_device_id)
    assert saved.reference_noise == 25.1 and saved.reference_nps_freq == 0.28
    assert saved.roi_geometry == geometry
    assert saved.hospital_name == "CHU Exemple"  # the pending edit was not lost
    assert dialog._edit_ref_noise.text() == "25,10"

    # Not offered for another scanner, nor without an analysis
    dialog._refresh_device_list(other.device_id)
    assert not dialog._btn_ref_from_analysis.isEnabled()
    no_analysis = DeviceManagerDialog(db, None, select_device_id=saved.device_id, current_image=scanner_image())
    assert not no_analysis._btn_ref_from_analysis.isEnabled()


def test_roi_geometry_is_shown_and_can_be_reset(qapp, db, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from cq_tdm.core.qc_history import QCRun
    from cq_tdm.core.roi_geometry import ROIGeometry

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    device = DeviceConfig.from_dicom("ACME", "CT 9000", "ST1", "SN42")
    device.roi_geometry = ROIGeometry.from_phantom(400, 0.5, 512, 512).frozen("2026-10-01")
    device.runs.append(QCRun(run_date="2026-10-01", series_uid="A"))
    db.save_device(device)

    dialog = DeviceManagerDialog(db, None, select_device_id=device.device_id)
    labels = dialog._labels_geometry
    assert labels["state"].text() == "figée depuis le contrôle du 01/10/2026"
    assert "matrice 512 × 512" in labels["format"].text() and "0,500 mm" in labels["format"].text()
    assert labels["central"].text() == "Ø 160 px (80,0 mm)"
    assert "Ø 40 px (20,0 mm)" in labels["peripheral"].text()
    assert "12,5 mm de la paroi interne" in labels["peripheral"].text()
    assert labels["nps"].text().startswith("64 × 64 px (32,0 mm)")
    assert dialog._label_dicom_serial.text() == "SN42"
    assert dialog._label_runs.text() == "1 (dernier le 01/10/2026)"
    assert dialog._btn_reset_geometry.isEnabled()

    dialog._edit_hospital.setText("CHU Exemple")  # typed but not saved yet
    dialog._reset_roi_geometry()
    saved = DeviceDatabase(db.db_path).get_device(device.device_id)
    assert saved.roi_geometry is None
    assert saved.hospital_name == "CHU Exemple"
    assert labels["state"].text().startswith("non figée")
    assert labels["central"].text() == "—"
    assert not dialog._btn_reset_geometry.isEnabled()
