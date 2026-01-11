"""DICOM loading and handling utilities."""

from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pydicom
from pydicom.dataset import Dataset


@dataclass
class DicomImage:
    """Represents a loaded DICOM image with relevant metadata."""

    # Image data
    pixel_array: np.ndarray

    # Patient info
    patient_id: str = ""
    patient_name: str = ""

    # Study info
    study_date: str = ""
    study_description: str = ""

    # Series info
    series_description: str = ""
    series_number: int = 0

    # Image info
    instance_number: int = 0
    slice_location: float = 0.0
    slice_thickness: float = 0.0

    # Scanner info
    manufacturer: str = ""
    model_name: str = ""
    station_name: str = ""
    device_serial_number: str = ""

    # Acquisition parameters
    kvp: float = 0.0
    tube_current: float = 0.0
    exposure_time: float = 0.0
    convolution_kernel: str = ""
    focal_spots: str = ""
    study_time: str = ""
    acquisition_time: str = ""

    # Geometry
    pixel_spacing: tuple[float, float] = (1.0, 1.0)
    rows: int = 0
    columns: int = 0

    # Reconstruction
    reconstruction_diameter: float = 0.0

    # Original file path
    file_path: str = ""

    @property
    def fov(self) -> float:
        """Field of view (reconstruction diameter)."""
        return self.reconstruction_diameter

    @property
    def pixel_size_mm(self) -> float:
        """Pixel size in mm (assumes square pixels)."""
        return self.pixel_spacing[0]


@dataclass
class DicomSeries:
    """Represents a series of DICOM images."""

    images: list[DicomImage] = field(default_factory=list)

    @property
    def num_images(self) -> int:
        return len(self.images)

    @property
    def is_empty(self) -> bool:
        return len(self.images) == 0

    def get_3d_array(self) -> np.ndarray:
        """Stack all images into a 3D array (slices, rows, cols)."""
        if self.is_empty:
            return np.array([])
        return np.stack([img.pixel_array for img in self.images], axis=0)

    def sort_by_location(self):
        """Sort images by slice location."""
        self.images.sort(key=lambda x: x.slice_location)

    def sort_by_instance(self):
        """Sort images by instance number."""
        self.images.sort(key=lambda x: x.instance_number)


def _get_attr(ds: Dataset, attr: str, default=None):
    """Safely get attribute from DICOM dataset."""
    try:
        value = getattr(ds, attr, default)
        if value is None:
            return default
        return value
    except Exception:
        return default


def _apply_modality_lut(ds: Dataset, pixel_array: np.ndarray) -> np.ndarray:
    """Apply rescale slope and intercept to convert to Hounsfield Units."""
    slope = _get_attr(ds, 'RescaleSlope', 1.0)
    intercept = _get_attr(ds, 'RescaleIntercept', 0.0)

    # Convert to float for calculations
    hu_array = pixel_array.astype(np.float64) * slope + intercept
    return hu_array


def load_dicom_file(file_path: str | Path) -> DicomImage:
    """
    Load a single DICOM file and extract relevant information.

    Args:
        file_path: Path to the DICOM file.

    Returns:
        DicomImage object with pixel data in Hounsfield Units.

    Raises:
        FileNotFoundError: If file doesn't exist.
        pydicom.errors.InvalidDicomError: If file is not valid DICOM.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"DICOM file not found: {file_path}")

    ds = pydicom.dcmread(str(file_path))

    # Get pixel array and convert to HU
    pixel_array = ds.pixel_array
    hu_array = _apply_modality_lut(ds, pixel_array)

    # Extract pixel spacing
    pixel_spacing = _get_attr(ds, 'PixelSpacing', [1.0, 1.0])
    if hasattr(pixel_spacing, '__iter__'):
        pixel_spacing = (float(pixel_spacing[0]), float(pixel_spacing[1]))
    else:
        pixel_spacing = (1.0, 1.0)

    # Extract patient name
    patient_name = _get_attr(ds, 'PatientName', '')
    if patient_name:
        patient_name = str(patient_name)

    # Extract focal spots (can be a list)
    focal_spots_raw = _get_attr(ds, 'FocalSpots', None)
    if focal_spots_raw is not None:
        if hasattr(focal_spots_raw, '__iter__') and not isinstance(focal_spots_raw, str):
            focal_spots = '/'.join(str(f) for f in focal_spots_raw)
        else:
            focal_spots = str(focal_spots_raw)
    else:
        focal_spots = ""

    return DicomImage(
        pixel_array=hu_array,
        patient_id=str(_get_attr(ds, 'PatientID', '')),
        patient_name=patient_name,
        study_date=str(_get_attr(ds, 'StudyDate', '')),
        study_description=str(_get_attr(ds, 'StudyDescription', '')),
        series_description=str(_get_attr(ds, 'SeriesDescription', '')),
        series_number=int(_get_attr(ds, 'SeriesNumber', 0)),
        instance_number=int(_get_attr(ds, 'InstanceNumber', 0)),
        slice_location=float(_get_attr(ds, 'SliceLocation', 0.0)),
        slice_thickness=float(_get_attr(ds, 'SliceThickness', 0.0)),
        manufacturer=str(_get_attr(ds, 'Manufacturer', '')),
        model_name=str(_get_attr(ds, 'ManufacturerModelName', '')),
        station_name=str(_get_attr(ds, 'StationName', '')),
        device_serial_number=str(_get_attr(ds, 'DeviceSerialNumber', '')),
        kvp=float(_get_attr(ds, 'KVP', 0.0)),
        tube_current=float(_get_attr(ds, 'XRayTubeCurrent', 0.0)),
        exposure_time=float(_get_attr(ds, 'ExposureTime', 0.0)),
        convolution_kernel=str(_get_attr(ds, 'ConvolutionKernel', '')),
        focal_spots=focal_spots,
        study_time=str(_get_attr(ds, 'StudyTime', '')),
        acquisition_time=str(_get_attr(ds, 'AcquisitionTime', '')),
        pixel_spacing=pixel_spacing,
        rows=int(_get_attr(ds, 'Rows', 0)),
        columns=int(_get_attr(ds, 'Columns', 0)),
        reconstruction_diameter=float(_get_attr(ds, 'ReconstructionDiameter', 0.0)),
        file_path=str(file_path),
    )


def load_dicom_folder(folder_path: str | Path) -> DicomSeries:
    """
    Load all DICOM files from a folder.

    Args:
        folder_path: Path to folder containing DICOM files.

    Returns:
        DicomSeries containing all loaded images, sorted by slice location.

    Raises:
        FileNotFoundError: If folder doesn't exist.
    """
    folder_path = Path(folder_path)
    if not folder_path.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")

    series = DicomSeries()

    # Find all potential DICOM files
    # DICOM files may have .dcm extension or no extension
    for file_path in folder_path.iterdir():
        if not file_path.is_file():
            continue

        # Skip non-DICOM files by extension
        suffix = file_path.suffix.lower()
        if suffix in ['.txt', '.pdf', '.xml', '.json', '.png', '.jpg', '.jpeg']:
            continue

        try:
            image = load_dicom_file(file_path)
            series.images.append(image)
        except Exception:
            # Skip files that can't be loaded as DICOM
            continue

    # Sort by slice location
    series.sort_by_location()

    return series


def detect_phantom_center(image: DicomImage) -> tuple[int, int]:
    """
    Detect the center of a water phantom in the image.

    Uses simple thresholding and centroid calculation.

    Args:
        image: DicomImage to analyze.

    Returns:
        Tuple of (row, col) for phantom center.
    """
    # Water phantom should be around 0 HU
    # Create mask for water-like values (-100 to 100 HU)
    mask = (image.pixel_array > -100) & (image.pixel_array < 100)

    # Find centroid of the mask
    rows, cols = np.where(mask)
    if len(rows) == 0:
        # Fallback to image center
        return image.rows // 2, image.columns // 2

    center_row = int(np.mean(rows))
    center_col = int(np.mean(cols))

    return center_row, center_col


def estimate_phantom_diameter(image: DicomImage, center: tuple[int, int]) -> float:
    """
    Estimate the diameter of a circular phantom in pixels.

    Args:
        image: DicomImage to analyze.
        center: (row, col) center of phantom.

    Returns:
        Estimated diameter in pixels.
    """
    # Create mask for water-like values
    mask = (image.pixel_array > -100) & (image.pixel_array < 100)

    center_row, center_col = center

    # Find extent in horizontal and vertical directions
    row_mask = mask[center_row, :]
    col_mask = mask[:, center_col]

    # Find connected region around center
    h_indices = np.where(row_mask)[0]
    v_indices = np.where(col_mask)[0]

    if len(h_indices) > 0 and len(v_indices) > 0:
        h_diameter = h_indices[-1] - h_indices[0]
        v_diameter = v_indices[-1] - v_indices[0]
        return (h_diameter + v_diameter) / 2

    # Fallback to reconstruction diameter if available
    if image.reconstruction_diameter > 0 and image.pixel_size_mm > 0:
        return image.reconstruction_diameter / image.pixel_size_mm

    # Default fallback
    return min(image.rows, image.columns) * 0.8
