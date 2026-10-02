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
    from cq_tdm.core.roi_geometry import ROIGeometry

    monkeypatch.setattr("cq_tdm.gui.main_window.ask", lambda *a, **k: True)
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
    from cq_tdm.core.qc_history import QCRun
    from cq_tdm.core.roi_geometry import ROIGeometry

    monkeypatch.setattr("cq_tdm.gui.main_window.ask", lambda *a, **k: True)
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


# --- unsaved edits (U-03) -----------------------------------------------------

@pytest.fixture
def two_devices(db):
    first = DeviceConfig.from_dicom("ACME", "CT 9000", "ST1", "SN42")
    second = DeviceConfig.from_dicom("OTHER", "X", "ST9", "SN9")
    db.save_device(first)
    db.save_device(second)
    return first, second


def _answer(monkeypatch, choice: str) -> list[str]:
    asked = []

    def fake(parent, title, text):
        asked.append(text)
        return choice

    monkeypatch.setattr("cq_tdm.gui.main_window.ask_save", fake)
    return asked


def test_leaving_an_edited_installation_asks_first(qapp, db, two_devices, monkeypatch):
    first, second = two_devices
    dialog = DeviceManagerDialog(db, None, select_device_id=first.device_id)
    assert not dialog._is_dirty()
    dialog._edit_hospital.setText("CHU Exemple")
    assert dialog._is_dirty()

    # "Annuler": the selection stays where it was and the text is still there
    asked = _answer(monkeypatch, "cancel")
    dialog._device_list.setCurrentRow(1)
    assert len(asked) == 1 and "ne sont pas enregistrées" in asked[0]
    assert dialog.selected_device_id == first.device_id
    assert dialog._device_list.currentRow() == 0
    assert dialog._edit_hospital.text() == "CHU Exemple"

    # "Enregistrer": saved, then the other installation is shown
    _answer(monkeypatch, "save")
    dialog._device_list.setCurrentRow(1)
    assert dialog.selected_device_id == second.device_id
    assert DeviceDatabase(db.db_path).get_device(first.device_id).hospital_name == "CHU Exemple"
    assert dialog._device_list.item(0).text().startswith("CHU Exemple")  # list entry renamed
    assert not dialog._is_dirty()


def test_discarding_edits_leaves_the_installation_untouched(qapp, db, two_devices, monkeypatch):
    first, second = two_devices
    dialog = DeviceManagerDialog(db, None, select_device_id=first.device_id)
    dialog._edit_inventory.setText("INV-1")
    _answer(monkeypatch, "discard")
    dialog._device_list.setCurrentRow(1)
    assert dialog.selected_device_id == second.device_id
    assert DeviceDatabase(db.db_path).get_device(first.device_id).inventory_number == ""
    assert first.inventory_number == ""


def test_closing_with_unsaved_edits_asks_first(qapp, db, two_devices, monkeypatch):
    first, _second = two_devices
    dialog = DeviceManagerDialog(db, None, select_device_id=first.device_id)
    dialog.show()
    dialog._edit_register["phantom_brand"].setText("PTW")

    _answer(monkeypatch, "cancel")
    dialog.reject()  # Escape, "Fermer" or the title bar cross
    assert dialog.isVisible()

    _answer(monkeypatch, "save")
    dialog.reject()
    assert not dialog.isVisible()
    assert DeviceDatabase(db.db_path).get_device(first.device_id).phantom_brand == "PTW"


def test_nothing_is_asked_without_edits_or_after_a_save(qapp, db, two_devices, monkeypatch):
    first, _second = two_devices
    monkeypatch.setattr("cq_tdm.gui.main_window.ask_save",
                        lambda *a: pytest.fail("no question expected"))
    dialog = DeviceManagerDialog(db, None, select_device_id=first.device_id)
    dialog._device_list.setCurrentRow(1)
    dialog._device_list.setCurrentRow(0)
    dialog._edit_hospital.setText("CHU Exemple")
    dialog._save_current_device()
    dialog._device_list.setCurrentRow(1)
    dialog.show()
    dialog.reject()
    assert not dialog.isVisible()


# --- reference values (U-24) and saved slices (P-17) --------------------------

def test_reference_question_shows_what_is_replaced():
    from cq_tdm.gui.main_window import reference_question

    text, accept = reference_question("CHU – CT 9000", None, None, 5.433, 0.2492, "analyse en cours", True)
    assert accept == "Définir comme références"
    assert text.startswith("Définir les valeurs de référence de « CHU – CT 9000 » (analyse en cours) ?")
    assert "σ : 5,43 UH" in text and "f SPB : 0,249 c/mm" in text and "→" not in text
    assert "figées" in text

    text, accept = reference_question("CHU – CT 9000", 8.18, 0.262, 3.3, 0.31, "contrôle du 15/09/2026", False)
    assert accept == "Remplacer"
    assert text.startswith("Remplacer les valeurs de référence")
    assert "σ : 8,18 → 3,30 UH" in text and "f SPB : 0,262 → 0,310 c/mm" in text
    assert "figées" not in text

    # No SPB in the new control: its reference is left alone, and the question says so
    text, _ = reference_question("X", 8.18, 0.262, 3.3, None, "contrôle du 15/09/2026", False)
    assert "f SPB : 0,262 c/mm (inchangée, SPB non mesuré)" in text
    text, _ = reference_question("X", None, 0.262, 3.3, 0.31, "analyse en cours", False)
    assert "σ : non définie → 3,30 UH" in text


def test_replacing_references_from_the_dialog_shows_the_old_values(qapp, db, monkeypatch):
    from cq_tdm.core.roi_geometry import ROIGeometry

    asked = []
    monkeypatch.setattr("cq_tdm.gui.main_window.ask",
                        lambda parent, title, text, accept, reject, **k: asked.append((text, accept)) or True)
    geometry = ROIGeometry.from_phantom(400, 0.5, 512, 512).frozen("2026-10-01")
    dialog = DeviceManagerDialog(db, None, current_image=scanner_image(), current_slices=(6, 1, 10),
                                 current_analysis=(25.1, 0.28, geometry), current_series_length=20)
    dialog._create_from_image()
    dialog._set_references_from_analysis()
    assert asked[-1][1] == "Définir comme références"

    dialog.current_analysis = (20.0, 0.30, geometry)
    dialog._set_references_from_analysis()
    text, accept = asked[-1]
    assert accept == "Remplacer"
    assert "σ : 25,10 → 20,00 UH" in text and "f SPB : 0,280 → 0,300 c/mm" in text


def test_slices_are_saved_with_the_length_of_their_series(qapp, db):
    dialog = DeviceManagerDialog(db, None, current_image=scanner_image(), current_slices=(3, 0, 9),
                                 current_series_length=20)
    dialog._create_from_image()
    saved = DeviceDatabase(db.db_path).get_device(dialog.selected_device_id)
    assert (saved.hu_slice_index, saved.slices_series_length) == (3, 20)
    assert dialog._label_hu_slice.text() == "4 (série de 20 coupes)"

    dialog.current_slices, dialog.current_series_length = (10, 5, 14), 24
    dialog._adopt_current_slices()
    saved = DeviceDatabase(db.db_path).get_device(dialog.selected_device_id)
    assert (saved.hu_slice_index, saved.slices_series_length) == (10, 24)
