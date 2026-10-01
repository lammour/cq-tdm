"""Loading a folder in the main window: series choice, unusable files, missing date."""

import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cq_tdm.core.device_database import DeviceConfig, DeviceDatabase  # noqa: E402
from cq_tdm.gui import main_window as mw  # noqa: E402

from .dicom_factory import LOCALIZER, write_ct_slice, write_series  # noqa: E402
from .test_phantom_detection import make_phantom  # noqa: E402

UID_A = "1.2.826.0.1.3680043.8.498.10"
UID_B = "1.2.826.0.1.3680043.8.498.20"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(mw, "save_app_config", lambda: None)
    window = mw.MainWindow()
    yield window
    # Pending debounce timers must not fire on a window that is being destroyed
    for timer in window.findChildren(mw.QTimer) + [
            window._hu_debounce_timer, window._nps_debounce_timer, window._ref_debounce_timer]:
        timer.stop()
    window.close()


@pytest.fixture
def shown(monkeypatch):
    """Texts of the message boxes shown (and closed at once)."""
    texts = []
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        lambda box: texts.append(f"{box.text()}\n{box.informativeText()}"))
    return texts


def phantom_series(folder: Path, count: int = 12, **kwargs) -> Path:
    """A series of noisy water phantom slices (wall detectable), 5 mm apart."""
    for i in range(count):
        write_ct_slice(folder, f"IM{i + 1:04d}", z=5.0 * i,
                       pixels=make_phantom(seed=i).pixel_array, **kwargs)
    return folder


def analyse(window):
    """Run the analyses the debounce timers would run."""
    window._run_debounced_hu_analysis()
    window._run_debounced_nps_analysis()


def test_one_series_loads_without_any_question(window, tmp_path, shown):
    write_series(tmp_path, 4)
    write_ct_slice(tmp_path, "TOPO", image_type=LOCALIZER, series_uid=UID_B)
    assert window._load_dicom_folder(str(tmp_path)) is True
    assert window._current_series.num_images == 4
    assert shown == []  # a topogram left out is expected: no warning box
    assert "4 coupes · 1 objet ignoré" in window._series_status.text()
    assert "TOPO : topogramme" in window._series_status.toolTip()


def test_user_chooses_between_the_series_of_a_folder(window, tmp_path, monkeypatch, shown):
    write_series(tmp_path, 5, prefix="A", series_uid=UID_A, series_number=2, description="Mou")
    write_series(tmp_path, 3, prefix="B", series_uid=UID_B, series_number=3, description="Dur")
    offered = []

    def pick_second(parent, title, label, items, current, editable):
        offered.extend(items)
        return items[1], True

    monkeypatch.setattr(mw.QInputDialog, "getItem", pick_second)
    assert window._load_dicom_folder(str(tmp_path)) is True
    assert offered == ["Série 2 — Mou — 5 coupes", "Série 3 — Dur — 3 coupes"]
    assert window._current_series.series_uid == UID_B
    assert window._current_series.num_images == 3

    # Cancelling the choice loads nothing new
    monkeypatch.setattr(mw.QInputDialog, "getItem", lambda *a: ("", False))
    assert window._load_dicom_folder(str(tmp_path)) is False
    assert window._current_series.series_uid == UID_B

    # The series of a recorded control is designated by its UID: no question
    monkeypatch.setattr(mw.QInputDialog, "getItem", lambda *a: pytest.fail("no choice expected"))
    assert window._load_dicom_folder(str(tmp_path), series_uid=UID_A) is True
    assert window._current_series.series_uid == UID_A


def test_unusable_files_are_reported(window, tmp_path, shown):
    write_series(tmp_path, 3)
    write_ct_slice(tmp_path, "IM9999", z=99.0, pixel_spacing=None)
    assert window._load_dicom_folder(str(tmp_path)) is True
    assert len(shown) == 1
    assert "3 images chargées, 1 fichier non utilisable" in shown[0]
    assert "taille de pixel absente" in shown[0]


def test_series_without_pixel_spacing_is_refused(window, tmp_path, shown):
    write_series(tmp_path, 3, pixel_spacing=None)
    assert window._load_dicom_folder(str(tmp_path)) is False
    assert window._current_series is None
    assert "Aucune coupe de ce dossier n'a pu être utilisée" in shown[0]
    assert "taille de pixel absente (champ DICOM PixelSpacing) (3 fichiers)" in shown[0]


def test_folder_with_only_a_topogram_is_explained(window, tmp_path, shown):
    write_ct_slice(tmp_path, "TOPO", image_type=LOCALIZER)
    assert window._load_dicom_folder(str(tmp_path)) is False
    assert "aucune coupe TDM axiale" in shown[0] and "topogramme" in shown[0]


def test_missing_date_is_flagged_and_typed_by_hand(window, tmp_path, shown):
    phantom_series(tmp_path, study_date="")
    assert window._load_dicom_folder(str(tmp_path)) is True
    analyse(window)

    assert window._control_date() == ("", False)
    assert "Date du contrôle absente des images" in window._install_summary.text()
    run = window._current_run_for_history()
    assert run.run_date == "" and run.date is None  # never the day of the analysis

    window._manual_control_date = "2026-09-15"  # what the date dialog stores
    window._update_results_display()
    assert window._control_date() == ("2026-09-15", True)
    assert "15/09/2026 (date saisie manuellement)" in window._install_summary.text()
    run = window._current_run_for_history()
    assert (run.run_date, run.run_date_manual) == ("2026-09-15", True)

    # A new series does not inherit the date typed for the previous one
    assert window._load_dicom_folder(str(tmp_path)) is True
    assert window._control_date() == ("", False)


def test_date_of_the_images_is_used_when_present(window, tmp_path, shown):
    phantom_series(tmp_path, study_date="20260301")
    assert window._load_dicom_folder(str(tmp_path)) is True
    analyse(window)
    assert window._control_date() == ("2026-03-01", False)
    assert "Date du contrôle" not in window._install_summary.text()
    assert window._ensure_control_date() is True  # nothing to ask


def test_phantom_not_found_is_flagged(window, tmp_path, shown):
    """No wall in the image: the ROIs are placed blind and the user must know."""
    rng = np.random.default_rng(0)
    for i in range(12):
        write_ct_slice(tmp_path, f"IM{i:04d}", z=5.0 * i, pixels=rng.normal(0, 10, (512, 512)))
    assert window._load_dicom_folder(str(tmp_path)) is True
    analyse(window)

    assert window._phantom_warnings() == [
        "fantôme non détecté sur la coupe UH",
        "fantôme non détecté sur la coupe médiane de la plage SPB",
    ]
    assert "Fantôme non détecté" in window.results_browser.toPlainText()
    assert window._current_analysis_for_reference() is None  # not a reference
    assert window._confirm_incomplete_export() is False  # asked, nothing clicked
    assert "sans repérer la paroi du fantôme" in shown[-1]


def test_detected_phantom_raises_no_warning(window, tmp_path, shown):
    phantom_series(tmp_path)
    assert window._load_dicom_folder(str(tmp_path)) is True
    analyse(window)
    assert window._phantom_warnings() == []
    assert "Fantôme non détecté" not in window.results_browser.toPlainText()


def test_series_without_identity_is_attached_by_hand(window, tmp_path, monkeypatch, shown):
    """An anonymised series names no scanner: it is neither recognised nor merged."""
    db = window._device_db
    other = DeviceConfig.from_dicom("", "", "", "")
    other.hospital_name = "Autre site"
    db.save_device(other)

    phantom_series(tmp_path, manufacturer="", model="", station="", serial="")
    assert window._load_dicom_folder(str(tmp_path)) is True
    assert window._current_device is None  # not "recognised" as the other installation
    assert "sans identification du scanner" in window.statusbar.currentMessage()
    assert "n'identifient pas le scanner" in window.history_panel._summary.text()

    dialog = mw.DeviceManagerDialog(db, window, current_image=window._current_image)
    assert dialog._btn_new_from_image.isEnabled()
    dialog._create_from_image()
    created = db.get_device(dialog.image_device_id)
    assert created is not None and created.device_id != other.device_id
    assert len(DeviceDatabase(db.db_path).get_all_devices()) == 2

    window._after_device_manager(dialog)
    assert window._current_device is created


def test_export_records_the_control_with_its_date_and_type(window, tmp_path, monkeypatch, shown):
    """End to end: undated series, date and type asked once, PDF written, run recorded."""
    folder = phantom_series(tmp_path / "dicom", study_date="")
    assert window._load_dicom_folder(str(folder)) is True
    analyse(window)
    dialog = mw.DeviceManagerDialog(window._device_db, window, current_image=window._current_image)
    dialog._create_from_image()
    window._after_device_manager(dialog)
    device = window._current_device
    assert device is not None
    window._artifact_result = False
    window._edit_ref_noise.setText("25,0")
    window._edit_ref_nps_freq.setText("0,500")

    asked = []

    def answer_export_dialog(export_dialog):
        asked.append(export_dialog)
        assert export_dialog._date is not None  # the images carry no date: it is asked
        export_dialog._date.setDate(mw.QDate(2026, 9, 15))
        export_dialog._type.setCurrentText("après intervention ou évolution logicielle")
        export_dialog._performed_by.setText("A. Martin")
        return mw.QDialog.DialogCode.Accepted

    pdf = tmp_path / "rapport.pdf"
    monkeypatch.setattr(mw.ExportDialog, "exec", answer_export_dialog)
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", lambda *a, **k: (str(pdf), ""))
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", lambda url: True)
    # The stability tests are judged (references given): only the dialog above is expected
    window._export_pdf()

    assert len(asked) == 1 and pdf.is_file() and pdf.stat().st_size > 10_000
    run = device.runs[-1]
    assert (run.run_date, run.run_date_manual) == ("2026-09-15", True)
    assert run.control_type == "après intervention ou évolution logicielle"
    assert run.performed_by == "A. Martin"
    assert run.pdf_path == str(pdf)
    # The geometry of this first control is frozen from its (typed) date
    assert device.roi_geometry is not None and device.roi_geometry.frozen_date == "2026-09-15"
    # The name is offered again next time
    assert mw.get_app_config().last_performed_by == "A. Martin"
