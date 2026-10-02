"""ROI geometry: sizes and offsets of every ROI, in pixels, relative to the phantom centre.

The geometry is computed from the detected phantom (inner water disc) the first
time, then frozen on the installation so that sizes and positions stay identical
from one control to the next (ANSM decision, uniformity test: "les positions et
les tailles des ROI doivent être identiques d'un contrôle à l'autre"). Only the
phantom centre is re-detected on each control, because the phantom is never put
back at exactly the same spot on the table.

A frozen geometry is only valid for the image format it was computed for (matrix
and pixel size): a protocol change invalidates it, see `matches`.
"""

from dataclasses import dataclass, asdict, fields, replace
import math
from typing import Optional

# HU ROIs (ANSM decision 9.1.7)
CENTRAL_FRACTION = 0.40  # central ROI diameter / phantom diameter
PERIPHERAL_FRACTION = 0.10  # peripheral ROI diameter / phantom diameter (upper bound)
PERIPHERAL_MIN_PIXELS = 100
PERIPHERAL_DISTANCE_MM = 12.5  # outer ROI edge to inner wall; the decision allows 10-15 mm

# NPS ROIs. The ANSM reference series all use 64 px squares with a field of
# view proportional to the phantom, i.e. a side of ~15 % of the inner diameter.
# Deriving the side from the diameter reproduces that geometry whatever the
# matrix and field of view (128 px for a 1024 matrix, never spilling into the
# wall on a large field of view).
NPS_ROI_FRACTION = 0.15
NPS_ROI_STEP = 8  # rounding step, so a ±1 px diameter jitter cannot change the size
NPS_ROI_MIN = 32
NPS_ROI_MAX = 128
NPS_CARDINAL_FRACTION = 0.455  # ROI centre offset / inner radius (ANSM JSON files)
NPS_DIAGONAL_FRACTION = 0.367  # per axis


@dataclass
class ROIGeometry:
    """All ROI sizes and offsets in pixels, relative to the phantom centre."""

    # Image format the geometry was computed for
    pixel_size_mm: float
    rows: int
    columns: int
    phantom_diameter_px: float  # inner diameter when computed (information)

    # HU ROIs
    central_radius: int
    peripheral_radius: int
    peripheral_distance: int  # phantom centre to peripheral ROI centre

    # NPS ROIs
    nps_roi_size: int
    nps_cardinal_distance: int
    nps_diagonal_offset: int  # per axis

    # ISO date of the control the geometry was frozen from; "" when it was
    # computed for the series being analysed
    frozen_date: str = ""

    @property
    def is_frozen(self) -> bool:
        return bool(self.frozen_date)

    @classmethod
    def from_phantom(
        cls,
        diameter_px: float,
        pixel_size_mm: float,
        rows: int,
        columns: int,
        peripheral_distance_mm: float = PERIPHERAL_DISTANCE_MM,
    ) -> "ROIGeometry":
        """Apply the sizing rules to a detected inner diameter."""
        if not pixel_size_mm > 0:
            raise ValueError(f"taille de pixel invalide ({pixel_size_mm} mm)")
        radius_px = diameter_px / 2.0

        central_radius = int(diameter_px * CENTRAL_FRACTION / 2)

        peripheral_radius = int(diameter_px * PERIPHERAL_FRACTION / 2)
        min_radius = int(math.ceil(math.sqrt(PERIPHERAL_MIN_PIXELS / math.pi)))
        peripheral_radius = max(peripheral_radius, min_radius)
        # Outer edge of the ROI at `peripheral_distance_mm` from the inner wall
        peripheral_distance = int(round(
            radius_px - peripheral_distance_mm / pixel_size_mm - peripheral_radius))

        nps_roi_size = int(round(diameter_px * NPS_ROI_FRACTION / NPS_ROI_STEP)) * NPS_ROI_STEP
        nps_roi_size = max(NPS_ROI_MIN, min(NPS_ROI_MAX, nps_roi_size))

        return cls(
            pixel_size_mm=float(pixel_size_mm),
            rows=int(rows),
            columns=int(columns),
            phantom_diameter_px=float(diameter_px),
            central_radius=central_radius,
            peripheral_radius=peripheral_radius,
            peripheral_distance=peripheral_distance,
            nps_roi_size=nps_roi_size,
            nps_cardinal_distance=int(radius_px * NPS_CARDINAL_FRACTION),
            nps_diagonal_offset=int(radius_px * NPS_DIAGONAL_FRACTION),
        )

    def matches(self, image) -> bool:
        """True when `image` has the matrix and pixel size this geometry was computed for."""
        if image.rows != self.rows or image.columns != self.columns:
            return False
        if self.pixel_size_mm <= 0:
            return False
        return abs(image.pixel_size_mm - self.pixel_size_mm) / self.pixel_size_mm < 0.005

    def frozen(self, date_iso: str) -> "ROIGeometry":
        """Copy of this geometry marked as frozen from the control of `date_iso`."""
        return replace(self, frozen_date=date_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["ROIGeometry"]:
        """Rebuild from a dict; None when the data is missing or unusable."""
        if not isinstance(data, dict):
            return None
        known = {f.name for f in fields(cls)}
        try:
            return cls(**{k: v for k, v in data.items() if k in known})
        except TypeError:
            return None
