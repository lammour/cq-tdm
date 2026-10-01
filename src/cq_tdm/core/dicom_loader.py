"""DICOM loading and handling utilities."""

from collections import Counter
from pathlib import Path
from dataclasses import dataclass, field
import numpy as np
import pydicom
from pydicom.dataset import Dataset

from .dicom_locator import is_dicom_candidate


class NotACTSlice(ValueError):
    """The file is a readable DICOM object but not an axial CT slice (topogram, dose report…)."""


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
    single_collimation_width: float = 0.0  # Detector row width (mm), DICOM (0018,9306)
    total_collimation_width: float = 0.0  # Total beam collimation (mm), DICOM (0018,9307)
    ctdi_vol: float = 0.0  # CTDIvol / IDSV in mGy, DICOM (0018,9345)
    ctdi_phantom: str = ""  # CTDI phantom, DICOM (0018,9346) code meaning
    acquisition_type: str = ""  # SPIRAL, SEQUENCED…, DICOM (0018,9302)
    image_type: str = ""  # ORIGINAL/PRIMARY/AXIAL…, DICOM (0008,0008), "/"-joined
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
    def collimation(self) -> str:
        """Collimation as "N × w mm" (rows × row width), "" when the tags are absent."""
        single, total = self.single_collimation_width, self.total_collimation_width
        if single <= 0 and total <= 0:
            return ""
        width = f"{single:g}".replace(".", ",")
        if single > 0 and total > 0:
            return f"{int(round(total / single))} × {width} mm"
        if single > 0:
            return f"{width} mm"
        return f"{total:g} mm".replace(".", ",")

    @property
    def acquisition_mode(self) -> str:
        """Acquisition mode in French: hélicoïdal (with pitch), axial, or the raw DICOM value."""
        kind = (self.acquisition_type or "").upper()
        if kind.startswith("SPIRAL") or (not kind and self.pitch > 0):
            pitch = f" (pitch {self.pitch:.3f})".replace(".", ",") if self.pitch > 0 else ""
            return f"hélicoïdal{pitch}"
        if kind.startswith("SEQUENCE"):
            return "axial (séquentiel)"
        return self.acquisition_type or ""

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


@dataclass(frozen=True)
class SeriesInfo:
    """One series of axial CT slices found in a folder."""

    series_uid: str
    series_number: int
    description: str
    num_images: int

    def label(self) -> str:
        """Text shown when the user has to choose between the series of a folder."""
        name = self.description or "sans description"
        plural = "s" if self.num_images > 1 else ""
        return f"Série {self.series_number} — {name} — {self.num_images} coupe{plural}"


@dataclass
class DicomSeries:
    """The slices of one CT series, loaded from a folder."""

    images: list[DicomImage] = field(default_factory=list)
    # Files that should have been slices of the series but could not be used
    # (unreadable, compressed, no pixel spacing…), as "filename : reason"
    load_errors: list[str] = field(default_factory=list)
    # Readable DICOM objects that are not axial CT slices (topogram, dose
    # report…): leaving them out is expected, as "filename : reason"
    skipped: list[str] = field(default_factory=list)
    # Every series of axial slices found in the folder, the loaded one included
    available_series: list[SeriesInfo] = field(default_factory=list)

    @property
    def num_images(self) -> int:
        return len(self.images)

    @property
    def is_empty(self) -> bool:
        return len(self.images) == 0

    @property
    def series_uid(self) -> str:
        return self.images[0].series_instance_uid if self.images else ""

    @property
    def other_series(self) -> list[SeriesInfo]:
        """The series of the folder that were not loaded."""
        return [s for s in self.available_series if s.series_uid != self.series_uid]

    def sort_by_location(self):
        """Sort images by slice location, then instance number for ties."""
        self.images.sort(key=lambda x: (x.slice_location, x.instance_number))


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


CT_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.2"
ENHANCED_CT_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.2.1"
# Cosine of the largest angle accepted between the slice normal and the z axis
_AXIAL_MIN_COSINE = 0.9
# Largest relative difference accepted between row and column spacing
_SQUARE_PIXEL_TOLERANCE = 0.001


def _image_type(ds: Dataset) -> list[str]:
    """Values of ImageType (0008,0008), upper case; [] when absent."""
    value = _get_attr(ds, 'ImageType', None)
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split("\\")
    return [str(v).strip().upper() for v in value]


def _is_compressed(ds: Dataset) -> bool:
    syntax = getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", None)
    return bool(syntax is not None and getattr(syntax, "is_compressed", False))


def _check_ct_slice(ds: Dataset) -> None:
    """Raise unless the dataset is an axial CT slice the analysis can use.

    The decision asks for axial images, uncompressed or decompressed without
    loss. A helical or a sequential acquisition both give axial slices
    (ImageType value 3 "AXIAL"); a topogram is "LOCALIZER". AcquisitionType and
    ScanOptions are not used: they are absent or vendor-specific on several
    scanners, and both acquisition modes are accepted anyway.
    """
    modality = str(_get_attr(ds, 'Modality', '') or '').strip().upper()
    if modality and modality != "CT":
        raise NotACTSlice(f"modalité {modality} : ce n'est pas une image de tomodensitométrie")
    sop_class = str(_get_attr(ds, 'SOPClassUID', '') or '')
    if sop_class == ENHANCED_CT_IMAGE_STORAGE:
        raise ValueError("format Enhanced CT (plusieurs coupes par fichier) non pris en charge : "
                         "exportez la série au format CT classique")
    if sop_class and sop_class != CT_IMAGE_STORAGE:
        raise NotACTSlice("objet DICOM qui n'est pas une coupe TDM (rapport de dose, capture d'écran…)")
    image_type = _image_type(ds)
    if "LOCALIZER" in image_type:
        raise NotACTSlice("topogramme (image de repérage)")
    orientation = _get_attr(ds, 'ImageOrientationPatient', None)
    if orientation is not None and len(orientation) == 6:
        try:
            rx, ry, rz, cx, cy, cz = (float(v) for v in orientation)
        except (TypeError, ValueError):
            rx = None
        if rx is not None:
            normal_z = rx * cy - ry * cx
            if abs(normal_z) < _AXIAL_MIN_COSINE:
                raise NotACTSlice("coupe non axiale (reconstruction coronale, sagittale ou oblique)")
    if str(_get_attr(ds, 'LossyImageCompression', '') or '').strip() == "01":
        raise ValueError("image compressée avec perte : la décision ANSM exige des images non "
                         "compressées ou décompressées sans perte")


def _pixel_spacing(ds: Dataset) -> tuple[float, float]:
    """PixelSpacing (row, column) in mm; ValueError when absent, null or not square.

    Every size in mm and every spatial frequency derives from it: a series
    without it cannot be analysed, and guessing 1 mm would give plausible but
    false results.
    """
    raw = _get_attr(ds, 'PixelSpacing', None)
    try:
        spacing = (float(raw[0]), float(raw[1]))
    except (TypeError, ValueError, IndexError):
        raise ValueError("taille de pixel absente (champ DICOM PixelSpacing)") from None
    if not all(np.isfinite(v) and v > 0 for v in spacing):
        raise ValueError(f"taille de pixel invalide (PixelSpacing = {spacing[0]:g} × {spacing[1]:g} mm)")
    if abs(spacing[0] - spacing[1]) > _SQUARE_PIXEL_TOLERANCE * max(spacing):
        raise ValueError(f"pixels non carrés ({spacing[0]:g} × {spacing[1]:g} mm) non pris en charge")
    return spacing


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
        NotACTSlice: The object is not an axial CT slice (topogram, dose report…).
        ValueError: The slice cannot be analysed (no pixel spacing, compressed…).
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"fichier DICOM introuvable : {file_path}")

    ds = pydicom.dcmread(str(file_path))
    _check_ct_slice(ds)
    pixel_spacing = _pixel_spacing(ds)

    # Get pixel array and convert to HU
    try:
        pixel_array = ds.pixel_array
    except Exception as e:
        if _is_compressed(ds):
            raise ValueError(
                "image compressée que ce logiciel ne sait pas décompresser : "
                "exportez la série sans compression") from e
        raise
    if pixel_array.ndim != 2:
        # RGB secondary captures (dose reports, screenshots) are not CT slices
        raise NotACTSlice("image en couleur ou à plusieurs plans (rapport de dose, capture d'écran)")
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
        single_collimation_width=float(_get_attr(ds, 'SingleCollimationWidth', 0.0)),
        total_collimation_width=float(_get_attr(ds, 'TotalCollimationWidth', 0.0)),
        ctdi_vol=float(_get_attr(ds, 'CTDIvol', 0.0)),
        ctdi_phantom=_ctdi_phantom(ds),
        acquisition_type=str(_get_attr(ds, 'AcquisitionType', '') or ''),
        image_type="/".join(_image_type(ds)),
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


def _majority(values):
    """Most frequent value of a non-empty sequence."""
    return Counter(values).most_common(1)[0][0]


def load_dicom_folder(folder_path: str | Path, series_uid: str | None = None) -> DicomSeries:
    """
    Load one series of axial CT slices from a folder.

    A folder exported from a PACS often holds more than the series to analyse:
    a topogram, a dose report, sometimes several reconstructions. Objects that
    are not axial CT slices are left out (``skipped``); the slices are grouped
    by SeriesInstanceUID and a single series is returned, never a mix. Within
    it, slices whose matrix or pixel size differs from the rest are refused.

    Args:
        folder_path: Path to folder containing DICOM files.
        series_uid: SeriesInstanceUID of the series to load. By default the
            series with the most slices; ``available_series`` lists them all so
            the caller can offer the choice.

    Returns:
        DicomSeries with the slices of one series, sorted by slice location.

    Raises:
        FileNotFoundError: If folder doesn't exist.
        NotADirectoryError: If the path is not a folder.
    """
    folder_path = Path(folder_path)
    if not folder_path.exists():
        raise FileNotFoundError(f"dossier introuvable : {folder_path}")
    if not folder_path.is_dir():
        raise NotADirectoryError(f"ce n'est pas un dossier : {folder_path}")

    series = DicomSeries()
    groups: dict[str, list[DicomImage]] = {}

    # DICOM files may have a .dcm extension or none
    for file_path in sorted(folder_path.iterdir()):
        if not file_path.is_file() or not is_dicom_candidate(file_path):
            continue
        try:
            image = load_dicom_file(file_path)
        except NotACTSlice as e:
            series.skipped.append(f"{file_path.name} : {e}")
            continue
        except Exception as e:
            # Keep the reason so the GUI can say why slices are missing
            # (compressed transfer syntax, no pixel spacing…)
            series.load_errors.append(f"{file_path.name} : {e}")
            continue
        groups.setdefault(image.series_instance_uid, []).append(image)

    series.available_series = sorted(
        (SeriesInfo(uid, images[0].series_number, images[0].series_description, len(images))
         for uid, images in groups.items()),
        key=lambda info: (-info.num_images, info.series_number))
    if not groups:
        return series

    if series_uid is not None and series_uid in groups:
        chosen = series_uid
    else:
        chosen = series.available_series[0].series_uid
    images = groups[chosen]

    # One matrix and one pixel size per series: the ROIs are placed in pixels
    # and the SPB uses a single pixel size for all its slices
    fmt = _majority([(img.rows, img.columns, round(img.pixel_size_mm, 4)) for img in images])
    for img in images:
        if (img.rows, img.columns, round(img.pixel_size_mm, 4)) == fmt:
            series.images.append(img)
        else:
            series.load_errors.append(
                f"{Path(img.file_path).name} : matrice ou taille de pixel différente "
                "du reste de la série")

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
    def detected(self) -> bool:
        """False for the fallback geometry: no wall was found, the ROIs are placed blind."""
        return self.num_edge_points > 0

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
