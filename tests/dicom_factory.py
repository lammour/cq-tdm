"""Synthetic DICOM CT files for the tests (no ANSM data needed)."""

from pathlib import Path

import numpy as np

AXIAL = ("ORIGINAL", "PRIMARY", "AXIAL")
LOCALIZER = ("ORIGINAL", "PRIMARY", "LOCALIZER")
SECONDARY_CAPTURE = "1.2.840.10008.5.1.4.1.1.7"

_DROP = object()  # value of a tag that must be absent from the file


def write_ct_slice(
    folder: Path,
    name: str,
    *,
    series_uid: str = "1.2.826.0.1.3680043.8.498.10",
    series_number: int = 2,
    description: str = "CQ EAU",
    z: float = 0.0,
    pixels: np.ndarray | None = None,
    rows: int = 32,
    columns: int = 32,
    pixel_spacing=(0.5, 0.5),
    image_type=AXIAL,
    modality: str = "CT",
    sop_class: str | None = None,
    orientation=(1, 0, 0, 0, 1, 0),
    manufacturer: str = "ACME",
    model: str = "CT 9000",
    station: str = "ST1",
    serial: str = "SN42",
    study_date: str = "20260915",
    **extra,
) -> Path:
    """Write one CT image file; a tag given as ``pixel_spacing=None`` etc. is left out.

    `pixels` are HU values (stored as int16 with RescaleIntercept -1024).
    """
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

    folder.mkdir(parents=True, exist_ok=True)
    sop_class = sop_class or CTImageStorage
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.SeriesInstanceUID = series_uid
    ds.SeriesNumber = series_number
    ds.SeriesDescription = description
    ds.Modality = modality
    if image_type is not None:
        ds.ImageType = list(image_type)
    ds.Manufacturer = manufacturer
    ds.ManufacturerModelName = model
    ds.StationName = station
    ds.DeviceSerialNumber = serial
    ds.StudyDate = study_date
    ds.KVP = 120
    ds.SliceThickness = 5
    ds.SliceLocation = z
    ds.ImagePositionPatient = [0, 0, z]
    if orientation is not None:
        ds.ImageOrientationPatient = list(orientation)
    if pixel_spacing is not None:
        ds.PixelSpacing = list(pixel_spacing)

    if pixels is None:
        pixels = np.zeros((rows, columns))
    stored = np.round(np.asarray(pixels) + 1024).astype(np.int16)
    ds.Rows, ds.Columns = stored.shape
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1
    ds.RescaleSlope = 1
    ds.RescaleIntercept = -1024
    ds.PixelData = stored.tobytes()

    for keyword, value in extra.items():
        setattr(ds, keyword, value)

    path = folder / name
    try:
        ds.save_as(str(path), enforce_file_format=True)  # pydicom 3
    except TypeError:
        ds.is_little_endian, ds.is_implicit_VR = True, False
        ds.save_as(str(path), write_like_original=False)  # pydicom 2
    return path


def write_series(folder: Path, count: int = 3, prefix: str = "IM", **kwargs) -> list[Path]:
    """Write `count` slices of one series, 5 mm apart."""
    return [write_ct_slice(folder, f"{prefix}{i + 1:04d}", z=5.0 * i, **kwargs) for i in range(count)]
