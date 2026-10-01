"""Confirm PDF generation breaks on '&' / '<' in free text (notes, folder, corrective action)."""
import sys, tempfile
from pathlib import Path
import numpy as np
sys.path.insert(0, "/home/user/cq-tdm/src")
sys.path.insert(0, "/home/user/cq-tdm")
import matplotlib.ft2font  # noqa
from cq_tdm.core.dicom_loader import DicomImage, DicomSeries
from cq_tdm.core.water_phantom import analyze_water_phantom, calculate_rois
from cq_tdm.core.qc_history import QCRun
from cq_tdm.reports.pdf_report import generate_pdf_report
from tests.test_phantom_detection import make_phantom

image = make_phantom()
water = analyze_water_phantom(image, calculate_rois(image))
tmp = Path(tempfile.mkdtemp())
cases = {
    "notes with <": dict(notes="Bruit < 5 HU observé"),
    "notes with &": dict(notes="Service CQ & radioprotection"),
    "dicom_folder with &": dict(dicom_folder=r"C:\CQ & Co\2026"),
    "corrective action with <": dict(history=[QCRun(run_date="2026-01-01", series_uid="A",
                                     corrective_action_date="2026-01-05", corrective_action="écart <1 HU après recalibration")]),
    "hospital name with &": dict(hospital_name="CHU A & B"),
}
for name, kw in cases.items():
    try:
        generate_pdf_report(tmp / "r.pdf", image, water, None, None, **kw)
        print(f"[{name}] OK")
    except Exception as e:
        print(f"[{name}] FAIL {type(e).__name__}: {str(e)[:110]}")
