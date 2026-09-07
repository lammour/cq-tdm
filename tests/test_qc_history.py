"""Unit tests for the QC results history (model, criteria, persistence, chart)."""

import json

import pytest

from cq_tdm.core.device_database import DeviceConfig, DeviceDatabase
from cq_tdm.core.qc_history import (
    NC,
    NCG,
    OK,
    PENDING,
    QCRun,
    dicom_date_to_iso,
    evaluate_run,
    noise_bounds,
    tolerance_band,
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
    assert evaluate_run(_run(water_ct=25.0))["water_ct"] == NC
    assert evaluate_run(_run(water_ct=25.1))["water_ct"] == NCG


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
    assert evaluate_run(_run(ref_noise=None, ref_nps_freq=None, artifacts_present=None))["overall"] == OK
    # Only the judged tests count: CT and uniformity are always judged


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


def test_report_filename_uses_scan_date():
    from cq_tdm.reports.pdf_report import generate_report_filename

    name = generate_report_filename("SIEMENS Edge", "666", date="20260315")
    assert name == "CQI-trimestriel_SIEMENS-Edge_666_2026-03-15.pdf"


def test_pdf_overall_status_matches_history_criteria():
    """The PDF badge must apply the same boundary rule (with epsilon) as evaluate_run."""
    from cq_tdm.core.water_phantom import ROIMeasurement, WaterPhantomResults
    from cq_tdm.reports.pdf_report import PDFReportGenerator, _ensure_reportlab

    _ensure_reportlab()

    def roi(mean, std=2.2):
        return ROIMeasurement("r", 0, 0, 5, mean, std, mean - 1, mean + 1, 100)

    results = WaterPhantomResults(
        central=roi(0.0), top=roi(1.0), right=roi(1.0), bottom=roi(1.0), left=roi(1.0),
        water_ct_number=0.0, uniformity=1.0, noise=2.2,
    )
    assert not hasattr(results, "uniformity_ncg")
    # 2.2 - 2.0 is 0.20000000000000018 in floating point: still conforme, like the history
    gen = PDFReportGenerator(reference_noise=2.0)
    status, _color, _action = gen._compute_overall_status(results, None, None)
    assert status == "CONFORME"
    assert evaluate_run(_run(noise=2.2, ref_noise=2.0))["noise"] == OK

    # Uniformity above 25 HU is NC, never NCG
    results.uniformity, results.uniformity_acceptable = 30.0, False
    status, _color, _action = gen._compute_overall_status(results, None, None)
    assert status == "NON CONFORME"
    assert evaluate_run(_run(uniformity=30.0))["uniformity"] == NC


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
