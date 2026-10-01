"""Unit tests for the QC results history (model, criteria, persistence, chart)."""

import json

import numpy as np
import pytest

from cq_tdm.core.device_database import DeviceConfig, DeviceDatabase
from cq_tdm.core.qc_history import (
    INCOMPLETE,
    NC,
    NC_OR_NCG,
    NCG,
    OK,
    PENDING,
    QCRun,
    dicom_date_to_iso,
    evaluate_run,
    noise_bounds,
    pending_reasons,
    tolerance_band,
    water_ct_status,
)


def _run(**kw) -> QCRun:
    base = dict(run_date="2026-03-15", series_uid="1.2.3", kvp=120, mas=200,
                water_ct=1.0, uniformity=2.0, noise=3.4, nps_freq=0.31,
                artifacts_present=False, ref_noise=3.4, ref_nps_freq=0.31)
    base.update(kw)
    return QCRun(**base)


# --- ANSM criteria ---------------------------------------------------------

def test_water_ct_thresholds():
    assert evaluate_run(_run(water_ct=7.0))["water_ct"] == OK
    assert evaluate_run(_run(water_ct=-7.1))["water_ct"] == NC
    assert evaluate_run(_run(water_ct=24.999))["water_ct"] == NC
    assert evaluate_run(_run(water_ct=25.001))["water_ct"] == NCG


def test_water_ct_of_exactly_25_is_left_to_the_ansm_text():
    """The decision puts ±25 HU in both tiers: neither NC nor NCG is asserted."""
    from cq_tdm.core.water_phantom import ROIMeasurement, WaterPhantomResults
    from cq_tdm.reports.pdf_report import PDFReportGenerator, _ensure_reportlab

    for value in (25.0, -25.0):
        st = evaluate_run(_run(water_ct=value))
        assert st["water_ct"] == st["overall"] == NC_OR_NCG
    # A sure NCG is not hidden by it, and it outranks a plain NC
    assert evaluate_run(_run(water_ct=25.0, artifacts_present=True))["overall"] == NC_OR_NCG

    _ensure_reportlab()
    roi = ROIMeasurement("r", 0, 0, 5, 25.0, 2.0, 24.0, 26.0, 100)
    results = WaterPhantomResults(central=roi, top=roi, right=roi, bottom=roi, left=roi,
                                  water_ct_number=25.0, uniformity=0.0, noise=2.0)
    gen = PDFReportGenerator()
    status, _color, action = gen._compute_overall_status(results, None, None)
    assert status == "NC OU NCG" and "9.1.7.3" in action
    section = " | ".join(_cells(gen._build_ct_number_section(results)))
    assert "NC ou NCG : merci de vous référer au texte ANSM" in section
    summary = " | ".join(_cells(gen._build_summary_section(results, None, None)))
    assert "NC ou NCG : merci de vous référer au texte ANSM" in summary


def test_uniformity_threshold():
    assert evaluate_run(_run(uniformity=7.0))["uniformity"] == OK
    assert evaluate_run(_run(uniformity=7.5))["uniformity"] == NC


def test_noise_band_uses_max_of_absolute_and_relative():
    assert noise_bounds(1.0) == (-0.2, 0.2)  # 10 % would be 0.1: absolute wins
    assert noise_bounds(3.4) == pytest.approx((-0.34, 0.34))  # relative wins
    assert evaluate_run(_run(noise=3.74, ref_noise=3.4))["noise"] == OK
    assert evaluate_run(_run(noise=3.75, ref_noise=3.4))["noise"] == NC
    assert evaluate_run(_run(ref_noise=None))["noise"] == PENDING


def test_nps_band_is_ten_percent():
    assert evaluate_run(_run(nps_freq=0.341, ref_nps_freq=0.31))["nps_freq"] == OK
    assert evaluate_run(_run(nps_freq=0.342, ref_nps_freq=0.31))["nps_freq"] == NC
    assert evaluate_run(_run(nps_freq=None))["nps_freq"] == PENDING
    assert evaluate_run(_run(ref_nps_freq=None))["nps_freq"] == PENDING


def test_overall_status_precedence():
    assert evaluate_run(_run())["overall"] == OK
    assert evaluate_run(_run(artifacts_present=True))["overall"] == NC
    assert evaluate_run(_run(water_ct=30, artifacts_present=True))["overall"] == NCG
    # "Conforme" requires every test to have been judged
    assert evaluate_run(_run(artifacts_present=None))["overall"] == INCOMPLETE
    assert evaluate_run(_run(ref_noise=None))["overall"] == INCOMPLETE
    assert evaluate_run(_run(nps_freq=None))["overall"] == INCOMPLETE
    # A non-conformity stands whatever else is missing
    assert evaluate_run(_run(water_ct=10, artifacts_present=None))["overall"] == NC
    assert evaluate_run(_run(water_ct=30, ref_noise=None))["overall"] == NCG


def test_pending_reasons_name_what_is_missing():
    st = evaluate_run(_run(ref_noise=None, artifacts_present=None))
    assert pending_reasons(st) == [
        "stabilité du bruit non évaluée (valeur de référence absente)",
        "inspection visuelle des artéfacts non réalisée",
    ]
    assert pending_reasons(evaluate_run(_run())) == []
    st = evaluate_run(_run(nps_freq=None))
    assert pending_reasons(st, nps_measured=False) == ["SPB non mesuré"]


def test_water_phantom_flags_follow_the_shared_criteria():
    """The analysis must not carry its own copy of the ±7 / ±25 HU thresholds."""
    from cq_tdm.core.water_phantom import analyze_water_phantom

    from .test_phantom_detection import make_phantom

    for offset, expected in ((0.0, OK), (12.0, NC), (40.0, NCG)):
        image = make_phantom(noise_sigma=5.0)
        water = np.abs(image.pixel_array) < 100
        image.pixel_array[water] += offset
        results = analyze_water_phantom(image)
        assert water_ct_status(results.water_ct_number) == expected
        assert results.water_ct_acceptable is (expected == OK)
        assert results.water_ct_ncg is (expected == NCG)


def test_tolerance_band():
    assert tolerance_band("water_ct", None, None) == (-7.0, 7.0, None)
    assert tolerance_band("noise", 3.4, None) == pytest.approx((3.06, 3.74, 3.4))
    assert tolerance_band("noise", None, None) is None
    assert tolerance_band("nps_freq", None, 0.3) == pytest.approx((0.27, 0.33, 0.3))


# --- model -----------------------------------------------------------------

def test_run_id_defaults_to_series_uid_or_is_unique():
    assert _run().run_id == "1.2.3"
    a, b = _run(series_uid=""), _run(series_uid="")
    assert a.run_id != b.run_id and a.run_id.startswith("2026-03-15-")


def test_dicom_date_to_iso():
    assert dicom_date_to_iso("20260315") == "2026-03-15"
    assert len(dicom_date_to_iso("")) == 10  # today, still ISO
    assert len(dicom_date_to_iso("garbage")) == 10


def test_round_trip_dict_ignores_unknown_keys_and_transient_flag():
    run = _run(notes="x")
    run.is_current = True
    d = run.to_dict()
    assert "is_current" not in d
    d["future_field"] = 42
    back = QCRun.from_dict(d)
    assert back == run and back.is_current is False


# --- persistence -----------------------------------------------------------

def _device() -> DeviceConfig:
    return DeviceConfig.from_dicom("SIEMENS", "SOMATOM", "CT01", "12345")


def test_database_persists_runs_and_replaces_same_series(tmp_path):
    db_path = tmp_path / "devices.json"
    db = DeviceDatabase(db_path)
    device = _device()
    db.save_device(device)

    assert db.add_run(device.device_id, _run(run_date="2026-01-10", series_uid="A")) is False
    assert db.add_run(device.device_id, _run(run_date="2026-04-10", series_uid="B")) is False
    # Same series exported again: replaced, not duplicated
    assert db.add_run(device.device_id, _run(run_date="2026-04-10", series_uid="B", noise=9.9)) is True

    reloaded = DeviceDatabase(db_path)
    runs = reloaded.get_device(device.device_id).runs
    assert [r.series_uid for r in runs] == ["A", "B"]
    assert runs[1].noise == 9.9
    assert json.loads(db_path.read_text(encoding="utf-8"))["version"] == 2

    assert reloaded.delete_run(device.device_id, "A") is True
    assert reloaded.delete_run(device.device_id, "A") is False
    assert [r.series_uid for r in DeviceDatabase(db_path).get_device(device.device_id).runs] == ["B"]


def test_unreadable_entries_do_not_hide_the_rest_and_survive_a_save(tmp_path):
    db_path = tmp_path / "devices.json"
    db = DeviceDatabase(db_path)
    first, second = _device(), DeviceConfig.from_dicom("GE", "Revolution", "CT02", "999")
    db.save_device(first)
    db.save_device(second)
    db.add_run(first.device_id, _run(series_uid="A"))

    data = json.loads(db_path.read_text(encoding="utf-8"))
    bad_run = {"series_uid": "B", "noise": 3.0}  # no run_date
    bad_device = {"device_id": "x", "runs": {"not": "a list"}}
    data["devices"][0]["runs"].append(bad_run)
    data["devices"].append(bad_device)
    db_path.write_text(json.dumps(data), encoding="utf-8")

    reloaded = DeviceDatabase(db_path)
    assert reloaded.load_error is None
    assert {d.device_id for d in reloaded.get_all_devices()} == {first.device_id, second.device_id}
    assert [r.series_uid for r in reloaded.get_device(first.device_id).runs] == ["A"]
    assert len(reloaded.load_warnings) == 2
    assert "1 contrôle illisible" in reloaded.load_warnings[0]
    assert "installation n° 3 illisible" in reloaded.load_warnings[1]

    # Saving must write the unread entries back, not drop them
    reloaded.add_run(first.device_id, _run(series_uid="C", run_date="2026-05-01"))
    saved = json.loads(db_path.read_text(encoding="utf-8"))
    assert bad_run in saved["devices"][0]["runs"]
    assert bad_device in saved["devices"]
    assert not db_path.with_suffix(".json.bak").exists()


def test_unparsable_file_still_sets_load_error_and_backs_up(tmp_path):
    db_path = tmp_path / "devices.json"
    db_path.write_text("{ not json", encoding="utf-8")
    db = DeviceDatabase(db_path)
    assert db.load_error and db.get_all_devices() == [] and db.load_warnings == []
    assert db_path.with_suffix(".json.bak").read_text(encoding="utf-8") == "{ not json"


def test_reexporting_a_series_keeps_its_corrective_action(tmp_path):
    db = DeviceDatabase(tmp_path / "devices.json")
    device = _device()
    db.save_device(device)
    recorded = _run(series_uid="A", water_ct=9.0)
    db.add_run(device.device_id, recorded)
    recorded.corrective_action_date, recorded.corrective_action = "2026-03-20", "recalibration"
    db.update_run(device.device_id, recorded)

    # Same series exported again (new PDF): the action entered meanwhile stays
    again = _run(series_uid="A", water_ct=9.0, pdf_path="new.pdf")
    assert db.add_run(device.device_id, again) is True
    run = DeviceDatabase(db.db_path).get_device(device.device_id).runs[0]
    assert (run.corrective_action_date, run.corrective_action) == ("2026-03-20", "recalibration")
    assert run.pdf_path == "new.pdf"

    # An action carried by the new run wins
    newer = _run(series_uid="A", corrective_action_date="2026-04-02",
                 corrective_action="tube remplacé")
    db.add_run(device.device_id, newer)
    run = DeviceDatabase(db.db_path).get_device(device.device_id).runs[0]
    assert (run.corrective_action_date, run.corrective_action) == ("2026-04-02", "tube remplacé")


def test_database_loads_version_1_file_without_runs(tmp_path):
    db_path = tmp_path / "devices.json"
    device = _device()
    v1 = {"version": 1, "devices": [{k: v for k, v in device.to_dict().items() if k != "runs"}]}
    db_path.write_text(json.dumps(v1), encoding="utf-8")
    db = DeviceDatabase(db_path)
    assert db.load_error is None
    assert db.get_device(device.device_id).runs == []


def test_update_run_rejects_foreign_run(tmp_path):
    db = DeviceDatabase(tmp_path / "devices.json")
    device = _device()
    db.save_device(device)
    with pytest.raises(ValueError):
        db.update_run(device.device_id, _run())


# --- chart -----------------------------------------------------------------

def test_trend_chart_renders_and_reports_hit_positions():
    import matplotlib.ft2font  # noqa: F401  (Linux FreeType workaround, see cq_tdm.main)
    from cq_tdm.core.trend_chart import render_trend_chart

    runs = [_run(run_date="2026-01-10", series_uid="A"), _run(run_date="2026-04-10", series_uid="B", noise=3.9)]
    chart = render_trend_chart(runs, "noise", 3.4, 0.31, width_px=400, height_px=200)
    assert chart.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert [h[2].series_uid for h in chart.hits] == ["A", "B"]
    for x, y, _ in chart.hits:
        assert 0 <= x <= 400 and 0 <= y <= 200

    empty = render_trend_chart([], "nps_freq", None, None, width_px=300, height_px=150)
    assert empty.hits == []


# --- consistency between the PDF, the results panel and the history ---------

def test_dicom_image_mas_prefers_exposure_tag():
    import numpy as np
    from cq_tdm.core.dicom_loader import DicomImage

    blank = np.zeros((4, 4))
    assert DicomImage(pixel_array=blank, exposure=150.0, tube_current=300.0, exposure_time=1000.0).mas == 150.0
    assert DicomImage(pixel_array=blank, tube_current=200.0, exposure_time=500.0).mas == 100.0
    assert DicomImage(pixel_array=blank, tube_current=200.0).mas == 0.0


def test_dicom_image_rotation_time_prefers_revolution_time():
    """RevolutionTime wins over ExposureTime, which can hold the whole spiral."""
    import numpy as np
    from cq_tdm.core.dicom_loader import DicomImage

    blank = np.zeros((4, 4))
    # A real GE spiral: 1 s per rotation, 16.758 s of acquisition
    spiral = DicomImage(pixel_array=blank, revolution_time=1.0, exposure_time=16758.0)
    assert spiral.rotation_time == 1.0
    # Without the tag, ExposureTime in ms is the only estimate left
    assert DicomImage(pixel_array=blank, exposure_time=500.0).rotation_time == 0.5
    assert DicomImage(pixel_array=blank).rotation_time == 0.0


def test_dicom_image_mas_fallback_uses_rotation_time():
    """The mAs fallback must not multiply the current by a whole spiral."""
    import numpy as np
    from cq_tdm.core.dicom_loader import DicomImage

    blank = np.zeros((4, 4))
    spiral = DicomImage(pixel_array=blank, tube_current=350.0,
                        revolution_time=1.0, exposure_time=16758.0)
    assert spiral.mas == 350.0  # not 350 × 16.758
    assert DicomImage(pixel_array=blank, tube_current=200.0,
                      revolution_time=0.5).mas == 100.0


def test_ctdi_phantom_names_are_shortened():
    """The phantom decides how CTDIvol reads, so it must survive extraction."""
    import pydicom
    from pydicom.dataset import Dataset
    from cq_tdm.core.dicom_loader import _ctdi_phantom

    def phantom(meaning: str) -> Dataset:
        item = Dataset()
        item.CodeMeaning = meaning
        ds = Dataset()
        ds.CTDIPhantomTypeCodeSequence = pydicom.Sequence([item])
        return ds

    assert _ctdi_phantom(phantom("IEC Head Dosimetry Phantom")) == "tête 16 cm"
    assert _ctdi_phantom(phantom("IEC Body Dosimetry Phantom")) == "corps 32 cm"
    assert _ctdi_phantom(phantom("Autre fantôme")) == "Autre fantôme"
    assert _ctdi_phantom(Dataset()) == ""


def test_report_filename_uses_scan_date():
    from cq_tdm.reports.pdf_report import generate_report_filename

    name = generate_report_filename("SIEMENS Edge", "666", date="20260315")
    assert name == "CQI-trimestriel_SIEMENS-Edge_666_2026-03-15.pdf"


def test_pdf_overall_status_matches_history_criteria():
    """The PDF badge must apply the same boundary rule (with epsilon) as evaluate_run."""
    from types import SimpleNamespace

    from cq_tdm.core.water_phantom import ROIMeasurement, WaterPhantomResults
    from cq_tdm.reports.pdf_report import (
        ArtifactInspectionResult,
        PDFReportGenerator,
        _ensure_reportlab,
    )

    _ensure_reportlab()

    def roi(mean, std=2.2):
        return ROIMeasurement("r", 0, 0, 5, mean, std, mean - 1, mean + 1, 100)

    results = WaterPhantomResults(
        central=roi(0.0), top=roi(1.0), right=roi(1.0), bottom=roi(1.0), left=roi(1.0),
        water_ct_number=0.0, uniformity=1.0, noise=2.2,
    )
    assert not hasattr(results, "uniformity_ncg")
    # 2.2 - 2.0 is 0.20000000000000018 in floating point: still conforme, like the history
    gen = PDFReportGenerator(reference_noise=2.0, reference_nps_freq=0.31)
    nps = SimpleNamespace(mean_frequency=0.31)
    no_artifact = ArtifactInspectionResult(artifacts_present=False)
    status, _color, _action = gen._compute_overall_status(results, nps, no_artifact)
    assert status == "CONFORME"
    assert evaluate_run(_run(noise=2.2, ref_noise=2.0))["noise"] == OK

    # Uniformity above 25 HU is NC, never NCG
    results.uniformity, results.uniformity_acceptable = 30.0, False
    status, _color, _action = gen._compute_overall_status(results, nps, no_artifact)
    assert status == "NON CONFORME"
    assert evaluate_run(_run(uniformity=30.0))["uniformity"] == NC


def test_pdf_never_says_conforme_when_a_test_was_not_judged():
    """No artifact inspection, no reference value or no SPB: the control is incomplete."""
    from types import SimpleNamespace

    from cq_tdm.core.water_phantom import ROIMeasurement, WaterPhantomResults
    from cq_tdm.reports.pdf_report import (
        ArtifactInspectionResult,
        PDFReportGenerator,
        _ensure_reportlab,
    )

    _ensure_reportlab()
    roi = ROIMeasurement("r", 0, 0, 5, 0.0, 2.0, -1.0, 1.0, 100)
    results = WaterPhantomResults(central=roi, top=roi, right=roi, bottom=roi, left=roi,
                                  water_ct_number=0.0, uniformity=1.0, noise=2.0)
    nps = SimpleNamespace(mean_frequency=0.31, num_slices=10)
    inspected = ArtifactInspectionResult(artifacts_present=False)
    complete = PDFReportGenerator(reference_noise=2.0, reference_nps_freq=0.31)

    status, _color, action = complete._compute_overall_status(results, nps, None)
    assert status == "CONTRÔLE INCOMPLET"
    assert "inspection visuelle des artéfacts non réalisée" in action

    status, _color, action = PDFReportGenerator()._compute_overall_status(results, nps, inspected)
    assert status == "CONTRÔLE INCOMPLET"
    assert "bruit" in action and "SPB" in action

    status, _color, action = complete._compute_overall_status(results, None, inspected)
    assert status == "CONTRÔLE INCOMPLET" and "SPB non mesuré" in action

    # A non-conformity is reported as such even when something else is missing
    results.water_ct_number = 30.0
    status, _color, _action = complete._compute_overall_status(results, nps, None)
    assert status == "NON-CONFORMITÉ GRAVE"
    results.water_ct_number = 0.0

    summary = " | ".join(_cells(PDFReportGenerator()._build_summary_section(results, nps, None)))
    assert "(référence absente)" in summary and "NON ÉVALUÉ" in summary
    assert "TEST NON RÉALISÉ" in summary
    assert "CONFORME" in summary  # CT number and uniformity are still judged


def test_scan_date_falls_back_when_study_date_is_blank():
    from pydicom.dataset import Dataset

    from cq_tdm.core.dicom_loader import _scan_date

    ds = Dataset()
    assert _scan_date(ds) == ""
    ds.InstanceCreationDate = "20241206"
    assert _scan_date(ds) == "20241206"
    ds.ContentDate = ""
    ds.AcquisitionDate = "20241205"
    assert _scan_date(ds) == "20241205"
    ds.StudyDate = "20241204"
    assert _scan_date(ds) == "20241204"


# --- register of operations (ANSM 3.2.2) ------------------------------------

def test_dicom_image_collimation_and_mode():
    import numpy as np
    from cq_tdm.core.dicom_loader import DicomImage

    blank = np.zeros((4, 4))
    assert DicomImage(pixel_array=blank, single_collimation_width=0.625,
                      total_collimation_width=40.0).collimation == "64 × 0,625 mm"
    assert DicomImage(pixel_array=blank, single_collimation_width=1.25).collimation == "1,25 mm"
    assert DicomImage(pixel_array=blank).collimation == ""
    assert DicomImage(pixel_array=blank, acquisition_type="SPIRAL", pitch=0.984).acquisition_mode == "hélicoïdal (pitch 0,984)"
    assert DicomImage(pixel_array=blank, pitch=1.0).acquisition_mode == "hélicoïdal (pitch 1,000)"
    assert DicomImage(pixel_array=blank, acquisition_type="SEQUENCED").acquisition_mode == "axial (séquentiel)"
    assert DicomImage(pixel_array=blank).acquisition_mode == ""


def test_iso_to_fr():
    from cq_tdm.core.qc_history import iso_to_fr
    assert iso_to_fr("2026-10-01") == "01/10/2026"
    assert iso_to_fr("2026-10-01T10:00:00") == "01/10/2026"
    assert iso_to_fr("") == ""
    assert iso_to_fr("n/a") == "n/a"


def test_register_fields_and_corrective_action_persist(tmp_path):
    db_path = tmp_path / "devices.json"
    db = DeviceDatabase(db_path)
    device = DeviceConfig.from_dicom("ACME", "CT1", "ST1", "SN1")
    device.phantom_brand, device.phantom_model, device.phantom_serial = "PTW", "Eau 20 cm", "123"
    device.clinical_protocol_origin = "Abdomen routine"
    device.reconstruction_algorithm = "iDose niveau 3"
    db.save_device(device)
    run = _run(series_uid="A")
    run.corrective_action_date, run.corrective_action = "2026-10-15", "recalibration"
    db.add_run(device.device_id, run)

    reloaded = DeviceDatabase(db_path).get_device(device.device_id)
    assert (reloaded.phantom_brand, reloaded.phantom_model, reloaded.phantom_serial) == ("PTW", "Eau 20 cm", "123")
    assert reloaded.clinical_protocol_origin == "Abdomen routine"
    assert reloaded.reconstruction_algorithm == "iDose niveau 3"
    assert reloaded.runs[0].corrective_action_date == "2026-10-15"
    assert reloaded.runs[0].corrective_action == "recalibration"
    # A file written before these fields existed still loads
    data = json.loads(db_path.read_text(encoding="utf-8"))
    for key in ("phantom_brand", "clinical_protocol_origin", "reconstruction_algorithm"):
        data["devices"][0].pop(key)
    data["devices"][0]["runs"][0].pop("corrective_action_date")
    db_path.write_text(json.dumps(data), encoding="utf-8")
    older = DeviceDatabase(db_path).get_device(device.device_id)
    assert older.phantom_brand == "" and older.runs[0].corrective_action_date == ""


def _cells(flowables) -> list[str]:
    """Every string found in the Tables and Paragraphs of a flowable list."""
    out = []
    for f in flowables:
        if hasattr(f, "_cellvalues"):
            for row in f._cellvalues:
                for cell in row:
                    out.append(cell.text if hasattr(cell, "text") else str(cell))
        elif hasattr(f, "text"):
            out.append(f.text)
    return out


def test_pdf_prints_the_register_items():
    import numpy as np
    from cq_tdm.core.dicom_loader import DicomImage
    from cq_tdm.reports.pdf_report import PDFReportGenerator, _ensure_reportlab

    _ensure_reportlab()
    gen = PDFReportGenerator(
        phantom_brand="PTW", phantom_model="Eau 20 cm", phantom_serial="123",
        clinical_protocol_origin="Abdomen routine", reconstruction_algorithm="iDose niveau 3",
        dicom_folder="/archives/cq/2026-10-01",
        history=[_run(series_uid="A", run_date="2026-07-01", corrective_action_date="2026-07-10",
                      corrective_action="recalibration")],
    )
    image = DicomImage(
        pixel_array=np.zeros((512, 512)), rows=512, columns=512, pixel_spacing=(0.5, 0.5),
        kvp=120.0, tube_current=200.0, exposure=100.0, revolution_time=0.5,
        acquisition_type="SPIRAL", pitch=0.984, single_collimation_width=0.625,
        total_collimation_width=40.0, focal_spots="1.2", convolution_kernel="STANDARD",
        slice_thickness=2.5, reconstruction_diameter=240.0, ctdi_vol=12.3, ctdi_phantom="corps 32 cm",
        series_instance_uid="1.2.826.0.1.3680043.2.1125.1.2345",
    )
    equipment = " | ".join(_cells(gen._build_equipment_section(image)))
    assert "PTW Eau 20 cm (n° série 123)" in equipment
    assert "Abdomen routine" in equipment and "iDose niveau 3" in equipment

    acquisition = " | ".join(_cells(gen._build_acquisition_section(image)))
    for expected in ("Charge (mAs) :", "100", "0,50 s", "hélicoïdal (pitch 0,984)", "64 × 0,625 mm",
                     "1.2", "512 × 512", "12,30 mGy (fantôme corps 32 cm)",
                     "1.2.826.0.1.3680043.2.1125.1.2345", "/archives/cq/2026-10-01"):
        assert expected in acquisition, expected

    history = " | ".join(_cells(gen._build_history_section()))
    assert "Contrôle du 01/07/2026 : action corrective le 10/07/2026 — recalibration" in history


def test_pdf_prints_roi_positions_and_generates(tmp_path):
    from cq_tdm.core.dicom_loader import DicomSeries
    from cq_tdm.core.nps import analyze_nps
    from cq_tdm.core.water_phantom import analyze_water_phantom, calculate_rois
    from cq_tdm.reports.pdf_report import PDFReportGenerator, generate_pdf_report
    from .test_phantom_detection import make_phantom

    series = DicomSeries(images=[make_phantom(seed=i) for i in range(3)])
    image = series.images[1]
    rois = calculate_rois(image, geometry=calculate_rois(image).geometry.frozen("2026-07-01"))
    water = analyze_water_phantom(image, rois)
    nps = analyze_nps(series, slice_range=(0, 2), geometry=rois.geometry)

    gen = PDFReportGenerator()
    text = " | ".join(_cells(gen._build_roi_positions_table(image, water, nps)))
    assert "figées depuis le contrôle de référence du 01/07/2026" in text
    assert "UH Centre" in text and "SPB 8 (droite)" in text
    assert f"Ø {2 * water.central.radius}" in text
    assert f"{nps.roi_size} × {nps.roi_size}" in text

    out = tmp_path / "rapport.pdf"
    generate_pdf_report(out, image, water, nps, None, phantom_brand="PTW",
                        dicom_folder=str(tmp_path), history=[_run(series_uid="A")])
    assert out.stat().st_size > 10_000
