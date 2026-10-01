"""Offscreen captures of CQ TDM for the UX review. Usage: python capture.py <scenario>

Scenarios: empty | home | unknown | loaded | light
Nothing is written to the repository or to ~/.config: the config singleton is
replaced by an in-memory AppConfig pointing at a scratchpad database.
"""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_LOGGING_RULES"] = "qt.gui.imageio=false"

OUT = Path(__file__).parent
SCEN = sys.argv[1] if len(sys.argv) > 1 else "empty"
DB = OUT / f"devices_{SCEN}.json"
if DB.exists():
    DB.unlink()

import numpy as np  # noqa: E402

import cq_tdm.core.app_config as app_config  # noqa: E402

cfg = app_config.AppConfig(device_database_path=str(DB), report_include_history=True)
if SCEN == "light":
    cfg.theme = "light"
app_config._app_config = cfg
app_config.AppConfig.save = lambda self: None  # never touch ~/.config

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog  # noqa: E402

from cq_tdm.main import _create_dark_palette  # noqa: E402
from cq_tdm.core.dicom_loader import DicomImage, DicomSeries  # noqa: E402
from cq_tdm.core.device_database import DeviceConfig, DeviceDatabase  # noqa: E402
from cq_tdm.core.qc_history import QCRun  # noqa: E402
from cq_tdm.gui import main_window as mw  # noqa: E402

app = QApplication(sys.argv)
app.setApplicationName("CQ TDM")
app.setStyle("Fusion")
if cfg.theme == "dark":
    app.setPalette(_create_dark_palette())

shot_n = [0]


def shot(widget, name):
    app.processEvents()
    app.processEvents()
    shot_n[0] += 1
    path = OUT / f"{SCEN}_{shot_n[0]:02d}_{name}.png"
    widget.grab().save(str(path))
    print("saved", path.name, widget.size())


# QMessageBox / QDialog.exec never block: grab and return
def _exec_grab(self):
    self.show()
    app.processEvents()
    shot(self, type(self).__name__)
    self.hide()
    return QMessageBox.StandardButton.No if isinstance(self, QMessageBox) else QDialog.DialogCode.Rejected


QMessageBox.exec = _exec_grab


def _static_box(icon):
    def f(parent, title, text, *args, **kwargs):
        box = QMessageBox(icon, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent)
        return _exec_grab(box)
    return staticmethod(f)


QMessageBox.warning = _static_box(QMessageBox.Icon.Warning)
QMessageBox.critical = _static_box(QMessageBox.Icon.Critical)
QMessageBox.information = _static_box(QMessageBox.Icon.Information)
QMessageBox.question = _static_box(QMessageBox.Icon.Question)
QDialog.exec = _exec_grab

# ---------------------------------------------------------------- synthetic data
PIXEL_MM = 0.469
WATER_RADIUS = 213.0
WALL_PX = 8


def make_image(i, n, noise=8.2, seed=0, center=(256.0, 256.0)):
    rng = np.random.default_rng(seed + i)
    rows = cols = 512
    y, x = np.ogrid[:rows, :cols]
    dist = np.hypot(y - center[0], x - center[1])
    img = np.full((rows, cols), -1000.0)
    img[dist <= WATER_RADIUS + WALL_PX] = 120.0
    water = dist <= WATER_RADIUS
    img[water] = rng.normal(-0.8, noise, size=int(water.sum()))
    # smooth a little so the NPS is not flat white noise
    from scipy.ndimage import gaussian_filter
    img[water] = gaussian_filter(img, 0.7)[water]
    img[500:512, :] = rng.normal(30.0, 5.0, size=(12, cols))
    return DicomImage(
        pixel_array=img, rows=rows, columns=cols, pixel_spacing=(PIXEL_MM, PIXEL_MM),
        reconstruction_diameter=240.0, patient_name="FANTOME EAU", patient_id="CQ-001",
        study_date="20260915", acquisition_date="20260915", study_time="091530",
        series_description="CQ FANTOME EAU 120kV", series_instance_uid="1.2.826.0.1.3680043.9.9999.1",
        series_number=3, instance_number=i + 1, slice_location=-50.0 + 5.0 * i, slice_thickness=5.0,
        manufacturer="GE MEDICAL SYSTEMS", model_name="Discovery RT", station_name="CT01",
        device_serial_number="SN-12345", kvp=120.0, tube_current=350.0, revolution_time=1.0,
        exposure=23.0, pitch=0.938, convolution_kernel="STANDARD", focal_spots="1.2",
        ctdi_vol=12.34, ctdi_phantom="BODY32", acquisition_type="SPIRAL",
        single_collimation_width=0.625, total_collimation_width=40.0,
    )


def make_series(n=24):
    return DicomSeries(images=[make_image(i, n) for i in range(n)])


def make_runs():
    rows = [
        ("2024-10-03", -1.2, 2.1, 8.10, 0.267, False),
        ("2025-01-09", -0.4, 1.1, 7.93, 0.265, False),
        ("2025-04-08", -0.6, 1.0, 9.27, 0.258, False),   # NC noise
        ("2025-07-12", -0.1, 1.1, 8.27, 0.270, False),
        ("2025-10-08", -1.7, 1.4, 8.24, 0.263, True),    # NC artifacts
        ("2026-01-07", -1.3, 1.5, 8.38, 0.261, False),
        ("2026-04-09", +0.6, 1.7, 8.19, 0.264, False),
        ("2026-06-10", +8.3, 1.3, 8.21, 0.262, False),   # NC water CT
    ]
    runs = []
    for i, (d, ct, u, nz, f, art) in enumerate(rows):
        runs.append(QCRun(
            run_date=d, series_uid=f"1.2.826.0.1.3680043.9.9999.{100 + i}", kvp=120, mas=23,
            slice_thickness=5.0, kernel="STANDARD", water_ct=ct, uniformity=u, noise=nz,
            nps_freq=f, artifacts_present=art, artifacts_description="Anneau fin en périphérie" if art else "",
            ref_noise=8.18, ref_nps_freq=0.262, hu_slice_index=11, nps_start_slice=7, nps_end_slice=16,
            pdf_path=f"/home/user/Documents/CQ/rapport_{d}.pdf", dicom_folder=f"/data/CQ/{d}",
            software_version="0.6.0", recorded_at=f"{d}T10:12:00",
            corrective_action_date="2025-04-15" if nz > 9 else "",
            corrective_action="Recalibration par le fabricant, nouveau contrôle conforme" if nz > 9 else "",
        ))
    return runs


def make_device(with_runs=True):
    dev = DeviceConfig.from_dicom("GE MEDICAL SYSTEMS", "Discovery RT", "CT01", "SN-12345")
    dev.hospital_name = "CHU Exemple"
    dev.hospital_location = "Imagerie 2"
    dev.device_name = "GE MEDICAL SYSTEMS Discovery RT"
    dev.commissioning_date = "12/03/2021"
    dev.serial_number = "123456"
    dev.inventory_number = "INV-042"
    dev.phantom_brand = "PTW"
    dev.phantom_model = "Fantôme d'eau 20 cm"
    dev.phantom_serial = "PH-77"
    dev.clinical_protocol_origin = "Abdomen standard"
    dev.reconstruction_algorithm = "ASiR-V 40 %"
    dev.reference_noise = 8.18
    dev.reference_nps_freq = 0.262
    dev.hu_slice_index = 11
    dev.nps_start_slice = 7
    dev.nps_end_slice = 16
    if with_runs:
        dev.runs = make_runs()
    return dev


# ---------------------------------------------------------------- scenarios
if SCEN in ("home", "loaded", "light"):
    db = DeviceDatabase(DB)
    dev = make_device()
    db.save_device(dev)
    cfg.last_device_id = dev.device_id

window = mw.MainWindow()
window.show()
app.processEvents()
print("default size after show:", window.size(), "minimum:", window.minimumSize())
shot(window, "default_size")
window.resize(1366, 768 - 40)  # taskbar + title bar on a 1366x768 screen
shot(window, "1366x728")

if SCEN == "empty":
    # Device manager with nothing at all
    dlg = mw.DeviceManagerDialog(window._device_db, window)
    dlg.show()
    shot(dlg, "DeviceManager_empty")
    dlg.close()
    window._show_help()
    window._show_shortcuts()
    window._show_about()
    window._export_pdf()  # "Aucune image chargée"
    rs = mw.ReportSettingsDialog(window)
    rs.show(); shot(rs, "ReportSettings"); rs.close()
    ne = mw.NotesEditorDialog("", window)
    ne.show(); shot(ne, "NotesEditor"); ne.close()
    window.resize(900, 600)
    shot(window, "min_900x600")

if SCEN == "home":
    window.resize(1366, 728)
    window.history_panel._table.selectRow(2)
    shot(window, "home_selected_row")
    window.history_panel._show_details()
    run = window.history_panel._selected_run()
    ca = mw.CorrectiveActionDialog(run, window)
    ca.show(); shot(ca, "CorrectiveAction"); ca.close()
    window.history_panel._delete_selected()  # confirmation box
    window.history_panel._metric_combo.setCurrentIndex(0)
    window.history_panel._draw_chart()
    shot(window, "home_trend_water_ct")
    window.history_panel._metric_combo.setCurrentIndex(3)
    window.history_panel._draw_chart()
    shot(window, "home_trend_nps")
    dlg = mw.DeviceManagerDialog(window._device_db, window, select_device_id=window._current_device.device_id)
    dlg.show(); shot(dlg, "DeviceManager_selected"); dlg.close()

if SCEN in ("unknown", "loaded", "light"):
    series = make_series()
    mw.load_dicom_folder = lambda p: series
    window.resize(1366, 728)
    window._load_dicom_folder("/data/CQ/2026-09-15/S3")
    app.processEvents()
    shot(window, "just_loaded_before_debounce")
    # fire the debounced analyses now
    window._hu_debounce_timer.stop(); window._run_debounced_hu_analysis()
    window._nps_debounce_timer.stop(); window._run_debounced_nps_analysis()
    app.processEvents()
    shot(window, "results_1366x728")
    window.resize(1600, 1000)
    shot(window, "results_1600x1000")
    window.resize(1366, 728)
    window._results_tabs.setCurrentWidget(window.history_panel)
    shot(window, "history_with_current_1366x728")
    window._results_tabs.setCurrentWidget(window.results_browser)
    # scroll results to the bottom (NPS plot + artefacts)
    sb = window.results_browser.verticalScrollBar(); sb.setValue(sb.maximum())
    shot(window, "results_scrolled_bottom")
    sb.setValue(0)

    if SCEN == "unknown":
        window._export_pdf()  # "Installation non enregistrée" question
        dlg = mw.DeviceManagerDialog(window._device_db, window, current_image=window._current_image,
                                     current_slices=(11, 7, 16),
                                     current_analysis=window._current_analysis_for_reference())
        dlg.show(); shot(dlg, "DeviceManager_unknown_image"); dlg.close()

    if SCEN in ("loaded", "light"):
        # Artifact inspection dialog
        from cq_tdm.gui.image_viewer import ArtifactInspectionDialog
        ad = ArtifactInspectionDialog(series.images, 11, window)
        ad.show(); shot(ad, "ArtifactInspection"); ad._on_present(); shot(ad, "ArtifactInspection_present"); ad.close()
        window._artifact_result = False
        window._update_results_display()
        # modified slices state
        window.image_viewer.set_hu_slice_index(13)
        window._hu_debounce_timer.stop(); window._run_debounced_hu_analysis()
        window._check_slice_values_modified()
        shot(window, "slices_modified")
        window._reset_slices_to_saved()
        window._hu_debounce_timer.stop(); window._run_debounced_hu_analysis()
        # Device manager with image + analysis
        dlg = mw.DeviceManagerDialog(window._device_db, window, select_device_id=window._current_device.device_id,
                                     current_image=window._current_image, current_slices=(11, 7, 16),
                                     current_analysis=window._current_analysis_for_reference())
        dlg.show(); shot(dlg, "DeviceManager_loaded"); dlg.close()
        # Image info dialog
        window._show_image_info()
        # W/L at ANSM artefact window in the main viewer
        window.image_viewer._set_preset(80, 0)
        shot(window, "wl_80_0")
        window.image_viewer._set_preset(400, 40)
        # ROI overlays hidden
        window.image_viewer.toggle_info.setChecked(False)
        shot(window, "infos_hidden")
        window.image_viewer.toggle_info.setChecked(True)

        if SCEN == "loaded":
            # ----- PDF demo
            from cq_tdm.reports import generate_pdf_report, ArtifactInspectionResult
            pdf = OUT / "demo_report.pdf"
            dev = window._current_device
            nps_start, nps_end = window.image_viewer.get_nps_slice_range()
            mid = nps_start + (nps_end - nps_start + 1) // 2
            generate_pdf_report(
                str(pdf), series.images[window.image_viewer.get_hu_slice_index()],
                window._current_results, window._nps_results,
                ArtifactInspectionResult(artifacts_present=False),
                hospital_name=dev.hospital_name, hospital_location=dev.hospital_location,
                device_name=dev.device_name, commissioning_date=dev.commissioning_date,
                serial_number=dev.serial_number, inventory_number=dev.inventory_number,
                reference_noise=dev.reference_noise, reference_nps_freq=dev.reference_nps_freq,
                nps_image=series.images[mid], notes="## Observations\n- Fantôme centré au laser\n- **À surveiller** : bruit proche de la limite",
                history=dev.runs, dicom_folder="/data/CQ/2026-09-15/S3",
                phantom_brand=dev.phantom_brand, phantom_model=dev.phantom_model, phantom_serial=dev.phantom_serial,
                clinical_protocol_origin=dev.clinical_protocol_origin,
                reconstruction_algorithm=dev.reconstruction_algorithm,
            )
            print("PDF written", pdf)
            # second PDF: NC case, no reference, no artefact inspection, empty fields
            pdf2 = OUT / "demo_report_nc.pdf"
            r = window._current_results
            r.water_ct_number = 9.4; r.water_ct_acceptable = False; r.water_ct_ncg = False
            generate_pdf_report(
                str(pdf2), series.images[11], r, window._nps_results, None,
                hospital_name="", hospital_location="", device_name="", commissioning_date="",
                serial_number="", inventory_number="", reference_noise=None, reference_nps_freq=None,
                nps_image=series.images[mid], history=[], dicom_folder="",
            )
            print("PDF written", pdf2)

print("done")
