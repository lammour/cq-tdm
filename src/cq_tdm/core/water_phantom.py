"""Water phantom analysis for CT quality control.

Implements tests according to ANSM decision of 18/12/2025:
- Water CT number (exactitude and stability)
- Uniformity

The noise magnitude of the control is not measured here: it comes from the NPS
ROIs over the analysed slices (NPSResult.noise). The standard deviation of the
central ROI (``central.std_hu``) is only shown for information.

ROI specifications:
- Central ROI: 40% of phantom diameter
- 4 Peripheral ROIs: at 12h, 3h, 6h, 9h (cardinal positions)
  - Size: ≤10% phantom diameter, minimum 100 pixels
  - Position: 10-15mm from phantom wall
"""

from dataclasses import dataclass
from typing import Optional
import numpy as np

from .dicom_loader import DicomImage, detect_phantom
from .qc_history import NCG, OK, STATUS_LABEL, uniformity_status, water_ct_status
from .roi_geometry import ROIGeometry, PERIPHERAL_DISTANCE_MM


@dataclass
class ROIDefinition:
    """Definition of a Region of Interest."""

    center_row: int
    center_col: int
    radius: int  # in pixels
    name: str = ""

    @property
    def area_pixels(self) -> int:
        """Approximate area in pixels."""
        return int(np.pi * self.radius ** 2)


@dataclass
class ROIMeasurement:
    """Measurement results from a single ROI."""

    name: str
    center_row: int
    center_col: int
    radius: int
    mean_hu: float
    std_hu: float
    min_hu: float
    max_hu: float
    num_pixels: int


@dataclass
class WaterPhantomROIs:
    """Complete set of ROIs for water phantom analysis."""

    central: ROIDefinition
    top: ROIDefinition  # 12h
    right: ROIDefinition  # 3h
    bottom: ROIDefinition  # 6h
    left: ROIDefinition  # 9h
    # Sizes and offsets the ROIs were built from (frozen on the device or computed)
    geometry: Optional[ROIGeometry] = None

    @property
    def peripheral(self) -> list[ROIDefinition]:
        """List of all peripheral ROIs."""
        return [self.top, self.right, self.bottom, self.left]

    @property
    def all_rois(self) -> list[ROIDefinition]:
        """List of all ROIs."""
        return [self.central, self.top, self.right, self.bottom, self.left]


@dataclass
class WaterPhantomResults:
    """Results from water phantom analysis."""

    # ROI measurements
    central: ROIMeasurement
    top: ROIMeasurement
    right: ROIMeasurement
    bottom: ROIMeasurement
    left: ROIMeasurement

    # Derived metrics
    water_ct_number: float = 0.0  # Mean of central ROI
    uniformity: float = 0.0  # Max deviation between central and peripheral

    # Acceptance criteria results
    water_ct_acceptable: bool = True  # Within ±7 HU
    water_ct_ncg: bool = False  # NCG if outside ±25 HU
    # Uniformity has no "grave" tier in the ANSM decision (only ±7 HU)
    uniformity_acceptable: bool = True  # Within ±7 HU from center

    # ROI geometry used for the measurement
    geometry: Optional[ROIGeometry] = None

    @property
    def peripheral(self) -> list[ROIMeasurement]:
        return [self.top, self.right, self.bottom, self.left]

    def to_dict(self) -> dict:
        """Convert to dictionary for reporting."""
        return {
            "water_ct_number": self.water_ct_number,
            "uniformity": self.uniformity,
            "central_mean": self.central.mean_hu,
            "central_std": self.central.std_hu,
            "top_mean": self.top.mean_hu,
            "right_mean": self.right.mean_hu,
            "bottom_mean": self.bottom.mean_hu,
            "left_mean": self.left.mean_hu,
            "water_ct_acceptable": self.water_ct_acceptable,
            "water_ct_ncg": self.water_ct_ncg,
            "uniformity_acceptable": self.uniformity_acceptable,
        }


def calculate_rois(
    image: DicomImage,
    center: Optional[tuple[int, int]] = None,
    diameter_pixels: Optional[float] = None,
    peripheral_distance_mm: float = PERIPHERAL_DISTANCE_MM,
    geometry: Optional[ROIGeometry] = None,
) -> WaterPhantomROIs:
    """
    Calculate ROI positions for water phantom analysis.

    Args:
        image: DicomImage to analyze.
        center: (row, col) phantom center. Auto-detected if None.
        diameter_pixels: Inner phantom diameter in pixels. Auto-detected if None
            (ignored when `geometry` is given).
        peripheral_distance_mm: Outer edge of the peripheral ROIs to the inner wall.
        geometry: Frozen ROI sizes and offsets to reuse (same installation, same
            image format). Only the centre is detected then, so sizes and
            positions relative to the phantom are identical to the reference.

    Returns:
        WaterPhantomROIs with all ROI definitions.
    """
    # The decision places the peripheral ROIs 10-15 mm from the INNER wall:
    # detect_phantom measures the water disc, not the outer edge of the wall
    if geometry is None:
        if center is None or diameter_pixels is None:
            detected = detect_phantom(image, initial_center=center)
            if center is None:
                center = detected.center
            if diameter_pixels is None:
                diameter_pixels = detected.diameter
        geometry = ROIGeometry.from_phantom(
            diameter_pixels, image.pixel_size_mm, image.rows, image.columns,
            peripheral_distance_mm)
    elif center is None:
        center = detect_phantom(image).center

    center_row, center_col = center
    g = geometry

    central = ROIDefinition(center_row=center_row, center_col=center_col,
                            radius=g.central_radius, name="Centre")
    top = ROIDefinition(center_row=center_row - g.peripheral_distance, center_col=center_col,
                        radius=g.peripheral_radius, name="12h (Haut)")
    right = ROIDefinition(center_row=center_row, center_col=center_col + g.peripheral_distance,
                          radius=g.peripheral_radius, name="3h (Droite)")
    bottom = ROIDefinition(center_row=center_row + g.peripheral_distance, center_col=center_col,
                           radius=g.peripheral_radius, name="6h (Bas)")
    left = ROIDefinition(center_row=center_row, center_col=center_col - g.peripheral_distance,
                         radius=g.peripheral_radius, name="9h (Gauche)")

    return WaterPhantomROIs(central=central, top=top, right=right, bottom=bottom, left=left,
                            geometry=geometry)


def create_circular_mask(shape: tuple[int, int], center: tuple[int, int], radius: int) -> np.ndarray:
    """
    Create a circular mask for ROI extraction.

    Args:
        shape: (rows, cols) shape of the mask.
        center: (row, col) center of the circle.
        radius: Radius of the circle in pixels.

    Returns:
        Boolean mask array.
    """
    rows, cols = shape
    center_row, center_col = center

    y, x = np.ogrid[:rows, :cols]
    distance = np.sqrt((y - center_row) ** 2 + (x - center_col) ** 2)

    return distance <= radius


def measure_roi(image: DicomImage, roi: ROIDefinition) -> ROIMeasurement:
    """
    Measure statistics within an ROI.

    Args:
        image: DicomImage to analyze.
        roi: ROI definition.

    Returns:
        ROIMeasurement with statistics.
    """
    mask = create_circular_mask(
        image.pixel_array.shape,
        (roi.center_row, roi.center_col),
        roi.radius,
    )

    values = image.pixel_array[mask]

    if len(values) == 0:
        raise ValueError(f"ROI '{roi.name}' contains no pixels (center={roi.center_row},{roi.center_col}, radius={roi.radius})")

    return ROIMeasurement(
        name=roi.name,
        center_row=roi.center_row,
        center_col=roi.center_col,
        radius=roi.radius,
        mean_hu=float(np.mean(values)),
        std_hu=float(np.std(values)),
        min_hu=float(np.min(values)),
        max_hu=float(np.max(values)),
        num_pixels=len(values),
    )


def analyze_water_phantom(
    image: DicomImage,
    rois: Optional[WaterPhantomROIs] = None,
) -> WaterPhantomResults:
    """
    Perform complete water phantom analysis.

    Args:
        image: DicomImage of water phantom.
        rois: Pre-calculated ROIs. Auto-calculated if None.

    Returns:
        WaterPhantomResults with all measurements and acceptance status.
    """
    # Calculate ROIs if not provided
    if rois is None:
        rois = calculate_rois(image)

    # Measure all ROIs
    central = measure_roi(image, rois.central)
    top = measure_roi(image, rois.top)
    right = measure_roi(image, rois.right)
    bottom = measure_roi(image, rois.bottom)
    left = measure_roi(image, rois.left)

    # Water CT number (from central ROI)
    water_ct = central.mean_hu

    # Uniformity: max absolute deviation between peripheral and central
    peripheral_means = [top.mean_hu, right.mean_hu, bottom.mean_hu, left.mean_hu]
    deviations = [abs(m - water_ct) for m in peripheral_means]
    uniformity = max(deviations)

    # Acceptance criteria (ANSM decision of 18/12/2025): one definition, shared
    # with the history and the report (qc_history)
    # (exactly ±25 HU is neither flag: read water_ct_status() for the verdict)
    ct_status = water_ct_status(water_ct)
    water_ct_acceptable = ct_status == OK
    water_ct_ncg = ct_status == NCG

    # The decision defines no NCG threshold for uniformity.
    uniformity_acceptable = uniformity_status(uniformity) == OK

    return WaterPhantomResults(
        geometry=rois.geometry,
        central=central,
        top=top,
        right=right,
        bottom=bottom,
        left=left,
        water_ct_number=water_ct,
        uniformity=uniformity,
        water_ct_acceptable=water_ct_acceptable,
        water_ct_ncg=water_ct_ncg,
        uniformity_acceptable=uniformity_acceptable,
    )


def format_results_text(results: WaterPhantomResults) -> str:
    """Format results as human-readable text."""
    lines = [
        "═══════════════════════════════════════",
        "      ANALYSE FANTÔME EAU",
        "═══════════════════════════════════════",
        "",
        "NOMBRE CT DE L'EAU",
        f"  Valeur centrale: {results.water_ct_number:+.1f} HU",
        "  Critère: ±7 HU (NCG: ±25 HU)",
        f"  Statut: {STATUS_LABEL[water_ct_status(results.water_ct_number)]}",
        "",
        "UNIFORMITÉ",
        f"  Écart max centre-périphérie: {results.uniformity:.1f} HU",
        "  Critère: ≤7 HU",
        f"  Statut: {'✓ CONFORME' if results.uniformity_acceptable else '✗ NON CONFORME'}",
        "",
        "BRUIT",
        f"  Écart-type central: {results.central.std_hu:.2f} HU",
        "",
        "DÉTAIL PAR ROI",
        f"  Centre:  {results.central.mean_hu:+6.1f} ± {results.central.std_hu:.1f} HU ({results.central.num_pixels} px)",
        f"  12h:     {results.top.mean_hu:+6.1f} ± {results.top.std_hu:.1f} HU ({results.top.num_pixels} px)",
        f"  3h:      {results.right.mean_hu:+6.1f} ± {results.right.std_hu:.1f} HU ({results.right.num_pixels} px)",
        f"  6h:      {results.bottom.mean_hu:+6.1f} ± {results.bottom.std_hu:.1f} HU ({results.bottom.num_pixels} px)",
        f"  9h:      {results.left.mean_hu:+6.1f} ± {results.left.std_hu:.1f} HU ({results.left.num_pixels} px)",
        "═══════════════════════════════════════",
    ]
    return "\n".join(lines)
