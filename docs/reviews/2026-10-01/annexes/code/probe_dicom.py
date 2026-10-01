"""Probe how load_dicom_file behaves on atypical DICOM headers (empty DS/IS, missing PixelSpacing...)."""
import sys, tempfile, traceback
from pathlib import Path
import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

sys.path.insert(0, "/home/user/cq-tdm/src")
from cq_tdm.core.dicom_loader import load_dicom_file, load_dicom_folder, _apply_modality_lut
from cq_tdm.core.roi_geometry import ROIGeometry
from cq_tdm.core import water_phantom as wp

def make_ds(path, **overrides):
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    fm.MediaStorageSOPInstanceUID = generate_uid()
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=fm, preamble=b"\0"*128)
    ds.SOPClassUID = fm.MediaStorageSOPClassUID
    ds.SOPInstanceUID = fm.MediaStorageSOPInstanceUID
    ds.Modality = "CT"
    ds.Rows = 64; ds.Columns = 64
    ds.BitsAllocated = 16; ds.BitsStored = 16; ds.HighBit = 15
    ds.PixelRepresentation = 1
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = (np.zeros((64, 64), dtype=np.int16)).tobytes()
    ds.PixelSpacing = [0.5, 0.5]
    ds.RescaleSlope = 1; ds.RescaleIntercept = -1024
    ds.KVP = 120
    ds.SeriesInstanceUID = generate_uid()
    for k, v in overrides.items():
        setattr(ds, k, v)
    ds.save_as(str(path), enforce_file_format=True)
    return ds

tmp = Path(tempfile.mkdtemp())
cases = {
    "baseline": {},
    "empty_KVP": {"KVP": ""},
    "empty_SeriesNumber": {"SeriesNumber": ""},
    "empty_SliceThickness": {"SliceThickness": ""},
    "empty_PixelSpacing": {"PixelSpacing": ""},
    "no_PixelSpacing": {"PixelSpacing": None},
    "empty_RescaleSlope": {"RescaleSlope": ""},
    "no_Rescale": {"RescaleSlope": None, "RescaleIntercept": None},
    "zero_PixelSpacing": {"PixelSpacing": [0, 0]},
    "KVP_nonnumeric_str": {"StudyDate": "2026010"},
}
for name, ov in cases.items():
    p = tmp / f"{name}.dcm"
    try:
        make_ds(p, **ov)
    except Exception as e:
        print(f"[{name}] could not write: {e!r}")
        continue
    try:
        img = load_dicom_file(p)
        print(f"[{name}] OK  pixel_spacing={img.pixel_spacing} kvp={img.kvp} sn={img.series_number} hu[0,0]={img.pixel_array[0,0]} dtype={img.pixel_array.dtype}")
    except Exception as e:
        print(f"[{name}] FAIL {type(e).__name__}: {e}")

# geometry with pixel size 0
try:
    g = ROIGeometry.from_phantom(300, 0.0, 512, 512)
    print("ROIGeometry pixel 0:", g)
except Exception as e:
    print("ROIGeometry.from_phantom(pixel_size=0) ->", type(e).__name__, e)

# load_dicom_folder on a file path
try:
    load_dicom_folder(tmp / "baseline.dcm")
except Exception as e:
    print("load_dicom_folder(file) ->", type(e).__name__, e)
