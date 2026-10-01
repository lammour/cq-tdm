"""DICOM loading and handling utilities."""

from pathlib import Path
from dataclasses import dataclass, field
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
    # Date of the scan (DICOM DA): StudyDate, else SeriesDate, AcquisitionDate,
    # ContentDate, InstanceCreationDate. Anonymised series often blank StudyDate.
    acquisition_date: str = ""
    study_description: str = ""

    # Series info
    series_description: str = ""
    series_instance_uid: str = ""
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
    tube_current: float = 0.0  # X-ray tube current (mA)
    exposure_time: float = 0.0  # Exposure time (ms), DICOM (0018,1150)
    revolution_time: float = 0.0  # Gantry rotation time (s), DICOM (0018,9305)
    exposure: float = 0.0  # Exposure (mAs), DICOM (0018,1152)
    pitch: float = 0.0  # Spiral pitch factor, DICOM (0018,9311); 0 if axial
    ctdi_vol: float = 0.0  # CTDIvol / IDSV in mGy, DICOM (0018,9345)
    ctdi_phantom: str = ""  # CTDI phantom, DICOM (0018,9346) code meaning
    acquisition_type: str = ""  # SPIRAL, SEQUENCED…, DICOM (0018,9302)
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

    @property
    def rotation_time(self) -> float:
        """Gantry rotation time in seconds; 0 when unknown.

        RevolutionTime is the reliable source. ExposureTime is only a fallback:
        on most scanners it holds the rotation time in ms, but on some it holds
        the whole acquisition instead (16 758 ms for a spiral whose rotation
        time is 1 s), so it is used only when RevolutionTime is absent.
        """
        if self.revolution_time > 0:
            return self.revolution_time
        if self.exposure_time > 0:
            return self.exposure_time / 1000.0
        return 0.0

    @property
    def mas(self) -> float:
        """Tube load in mAs: the Exposure tag, else current × rotation time.

        Returns 0 when unknown. The fallback uses the rotation time rather than
        ExposureTime, which on a spiral can be the duration of the whole
        acquisition and would inflate the load by more than a decade.
        """
        if self.exposure > 0:
            return self.exposure
        rotation = self.rotation_time
        if self.tube_current > 0 and rotation > 0:
            return self.tube_current * rotation
        return 0.0


@dataclass
class DicomSeries:
    """Represents a series of DICOM images."""

    images: list[DicomImage] = field(default_factory=list)
    # Files in the folder that could not be loaded, as "filename: reason"
    load_errors: list[str] = field(default_factory=list)

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
        """Sort images by slice location, then instance number for ties."""
        self.images.sort(key=lambda x: (x.slice_location, x.instance_number))

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


def _ctdi_phantom(ds: Dataset) -> str:
    """Short name of the CTDI phantom, from the code sequence (0018,9346).

    The phantom decides how CTDIvol reads: the same mGy on the 16 cm head
    phantom and on the 32 cm body phantom are not the same exposure.
    """
    seq = _get_attr(ds, 'CTDIPhantomTypeCodeSequence')
    if not seq:
        return ""
    meaning = str(_get_attr(seq[0], 'CodeMeaning', '') or '')
    lowered = meaning.lower()
    if 'head' in lowered:
        return "tête 16 cm"
    if 'body' in lowered:
        return "corps 32 cm"
    return meaning


_DATE_TAGS = ('StudyDate', 'SeriesDate', 'AcquisitionDate', 'ContentDate', 'InstanceCreationDate')


def _scan_date(ds: Dataset) -> str:
    """Date of the scan as DICOM DA (YYYYMMDD): first usable tag of ``_DATE_TAGS``.

    Anonymised series (ANSM reference images among them) often blank StudyDate
    while keeping a later tag; falling back avoids stamping the control with the
    day of the analysis.
    """
    for tag in _DATE_TAGS:
        value = str(_get_attr(ds, tag, '') or '').strip()
        if len(value) >= 8 and value[:8].isdigit():
            return value[:8]
    return ""


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
    if pixel_array.ndim != 2:
        # RGB secondary captures (dose reports, screenshots) are not CT slices
        raise ValueError(f"Not a single-frame grayscale image (shape {pixel_array.shape})")
    hu_array = _apply_modality_lut(ds, pixel_array)

    # Slice position: SliceLocation is optional; fall back to the z component of
    # ImagePositionPatient so slices are still ordered correctly without it.
    slice_location = _get_attr(ds, 'SliceLocation', None)
    if slice_location is None:
        position = _get_attr(ds, 'ImagePositionPatient', None)
        if position is not None and len(position) == 3:
            slice_location = position[2]
    try:
        slice_location = float(slice_location) if slice_location is not None else 0.0
    except (TypeError, ValueError):
        slice_location = 0.0

    # Convolution kernel can be multi-valued (e.g. Siemens ['Br40', '3'])
    kernel_raw = _get_attr(ds, 'ConvolutionKernel', '')
    if hasattr(kernel_raw, '__iter__') and not isinstance(kernel_raw, str):
        convolution_kernel = '/'.join(str(k) for k in kernel_raw)
    else:
        convolution_kernel = str(kernel_raw)

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
        acquisition_date=_scan_date(ds),
        study_description=str(_get_attr(ds, 'StudyDescription', '')),
        series_description=str(_get_attr(ds, 'SeriesDescription', '')),
        series_instance_uid=str(_get_attr(ds, 'SeriesInstanceUID', '')),
        series_number=int(_get_attr(ds, 'SeriesNumber', 0)),
        instance_number=int(_get_attr(ds, 'InstanceNumber', 0)),
        slice_location=slice_location,
        slice_thickness=float(_get_attr(ds, 'SliceThickness', 0.0)),
        manufacturer=str(_get_attr(ds, 'Manufacturer', '')),
        model_name=str(_get_attr(ds, 'ManufacturerModelName', '')),
        station_name=str(_get_attr(ds, 'StationName', '')),
        device_serial_number=str(_get_attr(ds, 'DeviceSerialNumber', '')),
        kvp=float(_get_attr(ds, 'KVP', 0.0)),
        tube_current=float(_get_attr(ds, 'XRayTubeCurrent', 0.0)),
        exposure_time=float(_get_attr(ds, 'ExposureTime', 0.0)),
        revolution_time=float(_get_attr(ds, 'RevolutionTime', 0.0)),
        pitch=float(_get_attr(ds, 'SpiralPitchFactor', 0.0)),
        ctdi_vol=float(_get_attr(ds, 'CTDIvol', 0.0)),
        ctdi_phantom=_ctdi_phantom(ds),
        acquisition_type=str(_get_attr(ds, 'AcquisitionType', '') or ''),
        exposure=float(_get_attr(ds, 'Exposure', 0.0)),
        convolution_kernel=convolution_kernel,
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
        except Exception as e:
            # Skip files that can't be loaded as DICOM, but keep the reason so the
            # GUI can explain an empty result (e.g. compressed transfer syntax)
            series.load_errors.append(f"{file_path.name}: {e}")
            continue

    # Sort by slice location
    series.sort_by_location()

    return series


# Phantom geometry detection
#
# The ANSM decision places the peripheral ROIs relative to the INNER wall of
# the phantom (water/wall boundary). The geometry is therefore measured as the
# water disc: an initial centre from the largest water-like connected
# component, then the water -> wall step is located on many rays from that
# centre and a circle is fitted to the step points. The fit gives the true
# centre and inner radius even when the initial centre is off, and the robust
# refit discards rays hitting an air bubble, the table or an artefact.

PHANTOM_WATER_MASK_HU = 100.0  # |HU| below this is "water-like" for the initial mask
PHANTOM_EDGE_THRESHOLD_HU = 50.0  # |HU| above this (sustained) marks the wall on a ray
PHANTOM_EDGE_MIN_RUN = 3  # samples above threshold needed to accept a wall crossing
PHANTOM_RAY_COUNT = 72  # one ray every 5 degrees
PHANTOM_FIT_RESIDUAL_PX = 2.0  # points farther than this from the circle are rejected
PHANTOM_FIT_MIN_POINTS = 8


@dataclass(frozen=True)
class PhantomGeometry:
    """Water disc of a phantom: centre and inner radius, in pixels (sub-pixel)."""

    center_row: float
    center_col: float
    radius: float
    num_edge_points: int = 0  # rays kept by the circle fit (0 = fallback geometry)

    @property
    def center(self) -> tuple[int, int]:
        """(row, col) centre rounded to the nearest pixel."""
        return int(round(self.center_row)), int(round(self.center_col))

    @property
    def diameter(self) -> float:
        return 2.0 * self.radius


def _initial_phantom_center(image: DicomImage) -> tuple[float, float]:
    """Centroid of the largest water-like connected component (excludes the table)."""
    from scipy import ndimage

    mask = np.abs(image.pixel_array) < PHANTOM_WATER_MASK_HU
    labels, count = ndimage.label(mask)
    if count == 0:
        return image.rows / 2.0, image.columns / 2.0
    sizes = ndimage.sum(mask, labels, index=np.arange(1, count + 1))
    largest = int(np.argmax(sizes)) + 1
    rows, cols = np.where(labels == largest)
    return float(np.mean(rows)), float(np.mean(cols))


def _ray_edge_points(
    pixel_array: np.ndarray, center_row: float, center_col: float, n_rays: int
) -> np.ndarray:
    """Water -> wall crossing on each ray from the centre, as (row, col) points.

    Each ray is sampled every pixel out to the nearest image border. The edge is
    the first sample where |HU| stays above PHANTOM_EDGE_THRESHOLD_HU for
    PHANTOM_EDGE_MIN_RUN consecutive samples, interpolated linearly for a
    sub-pixel position. Rays without a crossing are skipped.
    """
    from scipy.ndimage import map_coordinates, uniform_filter1d

    rows, cols = pixel_array.shape
    r_max = int(min(center_row, center_col, rows - 1 - center_row, cols - 1 - center_col))
    if r_max < PHANTOM_EDGE_MIN_RUN + 2:
        return np.empty((0, 2))

    pixels = np.asarray(pixel_array, dtype=float)
    # The walk starts in water by construction: check it once, robustly to noise
    r0, c0 = int(round(center_row)), int(round(center_col))
    patch = np.abs(pixels[max(0, r0 - 4):r0 + 5, max(0, c0 - 4):c0 + 5])
    if patch.size == 0 or np.median(patch) >= PHANTOM_EDGE_THRESHOLD_HU:
        return np.empty((0, 2))

    radii = np.arange(0, r_max + 1, dtype=float)
    angles = np.linspace(0.0, 2.0 * np.pi, n_rays, endpoint=False)
    points = []
    for theta in angles:
        ray_rows = center_row + radii * np.sin(theta)
        ray_cols = center_col + radii * np.cos(theta)
        profile = map_coordinates(pixels, [ray_rows, ray_cols], order=1, mode="nearest")
        # Light smoothing along the ray: a single noisy water pixel must not stop the walk
        magnitude = uniform_filter1d(np.abs(profile), size=3, mode="nearest")
        above = magnitude >= PHANTOM_EDGE_THRESHOLD_HU
        # First index where `above` holds for PHANTOM_EDGE_MIN_RUN consecutive samples
        run = np.convolve(above.astype(int), np.ones(PHANTOM_EDGE_MIN_RUN, dtype=int), mode="valid")
        hits = np.where(run == PHANTOM_EDGE_MIN_RUN)[0]
        if len(hits) == 0:
            continue
        i = int(hits[0])
        # Linear interpolation of the threshold crossing between samples i-1 and i
        m0, m1 = magnitude[i - 1], magnitude[i]
        frac = (PHANTOM_EDGE_THRESHOLD_HU - m0) / (m1 - m0) if m1 > m0 else 0.0
        r_edge = radii[i - 1] + float(np.clip(frac, 0.0, 1.0))
        points.append((center_row + r_edge * np.sin(theta), center_col + r_edge * np.cos(theta)))
    return np.array(points) if points else np.empty((0, 2))


def _fit_circle(points: np.ndarray) -> tuple[float, float, float] | None:
    """Algebraic (Kasa) least-squares circle fit: (center_row, center_col, radius)."""
    if len(points) < 3:
        return None
    y, x = points[:, 0], points[:, 1]
    a = np.column_stack([x, y, np.ones_like(x)])
    b = x ** 2 + y ** 2
    try:
        (cx2, cy2, c), *_ = np.linalg.lstsq(a, b, rcond=None)
    except np.linalg.LinAlgError:
        return None
    cx, cy = cx2 / 2.0, cy2 / 2.0
    r2 = c + cx ** 2 + cy ** 2
    if not np.isfinite(r2) or r2 <= 0:
        return None
    return float(cy), float(cx), float(np.sqrt(r2))


def _robust_circle(points: np.ndarray) -> tuple[float, float, float, int] | None:
    """Circle fit with iterative rejection of points far from the circle.

    The rejection threshold adapts to the current fit quality (3 x the median
    residual, never below PHANTOM_FIT_RESIDUAL_PX): a few gross outliers (air
    bubble, table contact) distort the first fit enough that a fixed threshold
    would discard the good points along with them.
    """
    kept = points
    for _ in range(10):
        fit = _fit_circle(kept)
        if fit is None:
            return None
        cr, cc, r = fit
        residual = np.abs(np.hypot(kept[:, 0] - cr, kept[:, 1] - cc) - r)
        threshold = max(PHANTOM_FIT_RESIDUAL_PX, 3.0 * float(np.median(residual)))
        inliers = residual <= threshold
        if inliers.all():
            break
        if inliers.sum() < PHANTOM_FIT_MIN_POINTS:
            return None
        kept = kept[inliers]
    fit = _fit_circle(kept)
    if fit is None:
        return None
    return fit[0], fit[1], fit[2], len(kept)


def _fallback_geometry(image: DicomImage, center_row: float, center_col: float) -> PhantomGeometry:
    """Geometry used when no wall can be found (e.g. not a phantom image)."""
    if image.reconstruction_diameter > 0 and image.pixel_size_mm > 0:
        diameter = image.reconstruction_diameter / image.pixel_size_mm
    else:
        diameter = min(image.rows, image.columns) * 0.8
    return PhantomGeometry(center_row, center_col, diameter / 2.0, 0)


def detect_phantom(
    image: DicomImage,
    initial_center: tuple[float, float] | None = None,
    n_rays: int = PHANTOM_RAY_COUNT,
) -> PhantomGeometry:
    """Detect the water disc (centre and inner radius) of a water phantom.

    Two passes: the circle fitted from the initial centre gives a better centre,
    and the rays cast from it cross the wall at normal incidence. The initial
    centre only needs to lie inside the water.

    Args:
        image: DicomImage (HU) of the phantom slice to analyse.
        initial_center: (row, col) starting centre; auto-detected if None.
        n_rays: Number of rays used to sample the wall.

    Returns:
        PhantomGeometry with sub-pixel centre and inner radius in pixels.
    """
    if initial_center is None:
        center_row, center_col = _initial_phantom_center(image)
    else:
        center_row, center_col = float(initial_center[0]), float(initial_center[1])

    best = None
    for _ in range(2):
        points = _ray_edge_points(image.pixel_array, center_row, center_col, n_rays)
        if len(points) < PHANTOM_FIT_MIN_POINTS:
            break
        fit = _robust_circle(points)
        if fit is None:
            break
        center_row, center_col, radius, kept = fit
        best = PhantomGeometry(center_row, center_col, radius, kept)

    if best is None:
        return _fallback_geometry(image, center_row, center_col)
    return best


def detect_phantom_center(image: DicomImage) -> tuple[int, int]:
    """Detect the centre of a water phantom, (row, col) in pixels.

    Thin wrapper over detect_phantom(); prefer that function when the radius
    is needed too, to avoid running the detection twice.
    """
    return detect_phantom(image).center


def estimate_phantom_diameter(image: DicomImage, center: tuple[int, int]) -> float:
    """Estimate the INNER diameter of a circular phantom in pixels.

    Thin wrapper over detect_phantom(), starting from the given centre.
    """
    return detect_phantom(image, initial_center=center).diameter
