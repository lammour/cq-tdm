"""Noise Power Spectrum (NPS/SPB) analysis for CT quality control.

Implements NPS calculation according to ANSM decision of 18/12/2025.

Requirements:
- 10 slices of water phantom
- 8 ROIs positioned in octagonal pattern for analysis
- 2D FFT to compute frequency-domain noise characteristics
"""

from dataclasses import dataclass, field
from typing import Optional
import numpy as np

from .dicom_loader import DicomImage, DicomSeries, detect_phantom, detect_phantom_center
from .roi_geometry import ROIGeometry

# numpy 2.0 renamed trapz -> trapezoid; scipy.integrate.trapezoid gives identical
# results but pulls in scipy.linalg/sparse/optimize, which are excluded from the
# frozen executables to keep them small.
_trapezoid = getattr(np, "trapezoid", None) or getattr(np, "trapz")

# The 1D spectrum extends to 1.375 × Nyquist, as in the iQMetrix-CT reference data
RADIAL_EXTENT = 1.375


@dataclass
class NPSROIPosition:
    """Position of a single NPS ROI.

    Coordinates are CENTER positions (not corners).
    """
    x: int  # Column (X coordinate) - CENTER of ROI
    y: int  # Row (Y coordinate) - CENTER of ROI
    side_square: int  # Side length of square ROI


@dataclass
class NPSROIConfig:
    """Configuration for NPS ROI positions, matching JSON export format."""
    phantom_name: str = ""
    measurement_date: str = ""
    dfov: float = 0.0  # Display Field of View in mm
    pixel_size: float = 0.0  # Pixel size in mm
    width_in_pixel: int = 512
    phantom_diameter: float = 0.0  # Phantom diameter in mm
    slice_start_mm: float = 0.0  # Start slice position in mm
    slice_end_mm: float = 0.0  # End slice position in mm
    rois: list[NPSROIPosition] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "NPSROIConfig":
        """Create NPSROIConfig from dictionary (JSON import).

        Note: JSON format stores ROI positions as TOP-LEFT CORNER coordinates.
        This method converts them to CENTER coordinates for internal use.
        """
        rois = []
        for roi in data.get("ROI", []):
            side = int(roi["side_square"])
            # JSON stores top-left corner, convert to center
            rois.append(NPSROIPosition(
                x=int(roi["X"]) + side // 2,
                y=int(roi["Y"]) + side // 2,
                side_square=side,
            ))
        section = data.get("section", {})
        return cls(
            phantom_name=data.get("phantom", ""),
            measurement_date=data.get("measurement_date", ""),
            dfov=float(data.get("DFOV", 0.0)),
            pixel_size=float(data.get("pixel_size", 0.0)),
            width_in_pixel=int(data.get("width_in_pixel", 512)),
            phantom_diameter=float(data.get("phantom_diameter", 0.0)),
            slice_start_mm=float(section.get("start", 0.0)),
            slice_end_mm=float(section.get("stop", 0.0)),
            rois=rois,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON export.

        Note: Internal ROI coordinates are CENTER positions.
        JSON format uses TOP-LEFT CORNER positions for compatibility
        with reference data format.
        """
        # Convert center coordinates to top-left corner for JSON export
        roi_list = []
        for roi in self.rois:
            roi_list.append({
                "X": float(roi.x - roi.side_square // 2),
                "Y": float(roi.y - roi.side_square // 2),
                "side_square": float(roi.side_square),
            })

        return {
            "type_of_file": "NPS",
            "phantom": self.phantom_name,
            "measurement_date": self.measurement_date,
            "DFOV": self.dfov,
            "pixel_size": self.pixel_size,
            "width_in_pixel": float(self.width_in_pixel),
            "phantom_diameter": self.phantom_diameter,
            "commentaire": "NPS - ROI's position given in pixel (top-left corner), section in mm",
            "section": {
                "start": self.slice_start_mm,
                "stop": self.slice_end_mm,
            },
            "ROI": roi_list,
        }


@dataclass
class ROIUniformityWarning:
    """Warning about non-uniform content in an NPS ROI."""
    roi_index: int  # 0-based ROI index
    slice_index: int  # 0-based slice index
    mean_hu: float  # Mean HU value in ROI
    std_hu: float  # Standard deviation in ROI
    message: str  # Human-readable warning message


@dataclass
class NPSResult:
    """Results from NPS analysis."""

    # 2D NPS data
    nps_2d: np.ndarray  # 2D NPS matrix
    frequencies_x: np.ndarray  # Frequency axis (cycles/mm)
    frequencies_y: np.ndarray

    # 1D radial NPS
    nps_radial: np.ndarray  # Ring-averaged NPS (raw)
    nps_radial_fit: np.ndarray  # Smoothed curve (11th order poly fit); not used for any result
    frequencies_radial: np.ndarray  # Frequencies up to 1.375 × Nyquist

    # Summary metrics
    mean_frequency: float  # Mean frequency (centroid) of the raw radial NPS (cycles/mm)
    average_nps: float  # Average NPS value
    total_noise_power: float  # Integral of NPS

    # Analysis parameters
    num_slices: int
    roi_size: int
    pixel_size_mm: float

    # ROI configuration for export
    roi_config: NPSROIConfig = field(default_factory=NPSROIConfig)

    # Warnings about non-uniform ROIs
    roi_warnings: list[ROIUniformityWarning] = field(default_factory=list)

    # 0-based indices of ROIs that were clipped by the image border on at least
    # one slice and therefore did not contribute to the spectrum
    skipped_rois: list[int] = field(default_factory=list)
    # ROI geometry the positions were built from (None when positions were given)
    geometry: Optional[ROIGeometry] = None

    # Noise magnitude of the control (ANSM 9.1.7.2: "déterminer le SPB et le
    # bruit sur l'ensemble des 10 coupes"): the standard deviation of the HU
    # values is taken in every NPS ROI of every slice, and these are averaged
    # (8 ROIs on 10 slices: mean of 80 standard deviations). Same value as
    # "Noise (HU)" of the iQMetrix-CT reference results.
    noise: float = 0.0
    noise_roi_count: int = 0  # number of standard deviations averaged

    def to_dict(self) -> dict:
        """Convert to dictionary for reporting."""
        return {
            "mean_frequency": self.mean_frequency,
            "noise": self.noise,
            "average_nps": self.average_nps,
            "total_noise_power": self.total_noise_power,
            "num_slices": self.num_slices,
            "roi_size": self.roi_size,
            "pixel_size_mm": self.pixel_size_mm,
        }


def extract_roi_for_nps(
    image: DicomImage,
    center: Optional[tuple[int, int]] = None,
    roi_size: int = 128,
) -> np.ndarray:
    """
    Extract a square ROI for NPS analysis.

    Args:
        image: DicomImage to analyze.
        center: (row, col) ROI center. Auto-detected if None.
        roi_size: Size of square ROI in pixels.

    Returns:
        2D numpy array of the ROI.
    """
    if center is None:
        center = detect_phantom_center(image)

    center_row, center_col = center
    half_size = roi_size // 2

    # Extract ROI
    row_start = center_row - half_size
    row_end = center_row + half_size
    col_start = center_col - half_size
    col_end = center_col + half_size

    # Ensure within bounds
    row_start = max(0, row_start)
    row_end = min(image.rows, row_end)
    col_start = max(0, col_start)
    col_end = min(image.columns, col_end)

    return image.pixel_array[row_start:row_end, col_start:col_end]


def detrend_roi(roi: np.ndarray) -> np.ndarray:
    """
    Remove low-frequency trends from ROI.

    Uses 2D polynomial fitting to remove background variations.

    Args:
        roi: 2D ROI array.

    Returns:
        Detrended ROI.
    """
    rows, cols = roi.shape

    # Create coordinate grids
    x = np.arange(cols)
    y = np.arange(rows)
    X, Y = np.meshgrid(x, y)

    # Fit 2nd order polynomial
    # Flatten for fitting
    X_flat = X.flatten()
    Y_flat = Y.flatten()
    Z_flat = roi.flatten()

    # Design matrix for 2nd order polynomial
    A = np.column_stack([
        np.ones_like(X_flat),
        X_flat,
        Y_flat,
        X_flat ** 2,
        Y_flat ** 2,
        X_flat * Y_flat,
    ])

    # Least squares fit
    coeffs, _, _, _ = np.linalg.lstsq(A, Z_flat, rcond=None)

    # Evaluate polynomial
    background = (
        coeffs[0] +
        coeffs[1] * X +
        coeffs[2] * Y +
        coeffs[3] * X ** 2 +
        coeffs[4] * Y ** 2 +
        coeffs[5] * X * Y
    )

    return roi - background


def compute_nps_2d(
    roi: np.ndarray,
    pixel_size_mm: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Compute 2D Noise Power Spectrum.

    Matches NPWE3 implementation: no windowing, simple normalization.

    Args:
        roi: 2D ROI array (should be detrended).
        pixel_size_mm: Pixel size in mm.

    Returns:
        Tuple of (nps_2d, freq_x, freq_y).
    """
    rows, cols = roi.shape

    # No windowing - direct FFT of detrended ROI
    fft_2d = np.fft.fft2(roi)
    fft_shifted = np.fft.fftshift(fft_2d)

    # Compute power spectrum with NPWE3 normalization
    roi_area_pixels = rows * cols
    pixel_area = pixel_size_mm * pixel_size_mm
    nps_2d = (np.abs(fft_shifted) ** 2) * pixel_area / roi_area_pixels

    # Frequency axes (NPWE3 style)
    freq_x = np.fft.fftshift(np.fft.fftfreq(cols)) / pixel_size_mm
    freq_y = np.fft.fftshift(np.fft.fftfreq(rows)) / pixel_size_mm

    return nps_2d, freq_x, freq_y


def radial_average(
    nps_2d: np.ndarray,
    freq_x: np.ndarray,
    freq_y: np.ndarray,
    pixel_size_mm: float = 1.0,
    num_bins: int = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Reduce the 2D NPS to a 1D radial NPS by averaging over rings.

    Every sample of the 2D spectrum is taken at its own distance r (in samples)
    from the zero frequency. Ring k collects the samples with k ≤ r < k + 1,
    is averaged over the samples that exist, and is assigned the frequency
    k / (N · pixel size).

    This is the convention that reproduces the iQMetrix-CT reference spectra
    of the ANSM image bank bin for bin, including beyond Nyquist where only
    the corners of the 2D spectrum hold data. The samples of ring k sit on
    average near k + 0.5, so the curve is shifted towards low frequencies by a
    fraction of a bin compared with a radius-exact labelling; this is part of
    the reference method and must not be "corrected" (see README).

    Args:
        nps_2d: 2D NPS matrix (square, zero frequency at index N // 2).
        freq_x: X frequency axis (unused, kept for the call signature).
        freq_y: Y frequency axis (unused, kept for the call signature).
        pixel_size_mm: Pixel size in mm.
        num_bins: Number of rings (default: up to 1.375 × Nyquist, as iQMetrix).

    Returns:
        Tuple of (nps_radial, frequencies_radial).
    """
    rows, cols = nps_2d.shape
    if rows != cols:
        raise ValueError(f"NPS array must be square, got {rows}x{cols}")
    fft_size = rows

    # iQMetrix extends to 1.375 × Nyquist (into the corners of the 2D spectrum):
    # int(N/2 × 1.375) + 1 rings, 45 for a 64 px ROI
    if num_bins is None:
        num_bins = int(fft_size // 2 * RADIAL_EXTENT) + 1

    # After fftshift the zero frequency is at index N // 2 on both axes
    center = fft_size // 2
    row_idx, col_idx = np.indices(nps_2d.shape)
    ring = np.floor(np.hypot(row_idx - center, col_idx - center)).astype(int)

    inside = ring < num_bins
    sums = np.bincount(ring[inside], weights=nps_2d[inside], minlength=num_bins)
    counts = np.bincount(ring[inside], minlength=num_bins)
    nps_r = np.divide(sums, counts, out=np.zeros(num_bins), where=counts > 0)

    # Exact for any ROI size: one ring is one sample of the 2D spectrum wide
    freq_r = np.arange(num_bins) / (fft_size * pixel_size_mm)

    return nps_r, freq_r


def mean_frequency_of(frequencies: np.ndarray, nps_values: np.ndarray) -> float:
    """Mean frequency of a 1D NPS: centroid ∫f·NPS(f)df / ∫NPS(f)df of the raw curve.

    Same rule as iQMetrix-CT ("Average Frequency"): computed on the raw radial
    spectrum over its whole range, not on the polynomial fit.
    """
    total_power = _trapezoid(nps_values, frequencies)
    if total_power <= 0:
        return 0.0
    return float(_trapezoid(frequencies * nps_values, frequencies) / total_power)


def fit_nps_polynomial(
    frequencies: np.ndarray,
    nps_values: np.ndarray,
    degree: int = 11,
) -> np.ndarray:
    """
    Fit polynomial to 1D NPS curve.

    11th degree polynomial, as the "fit" column of the iQMetrix reference data.
    It is a smoothed curve for display and comparison only: the mean frequency
    is computed on the raw spectrum (see mean_frequency_of).

    Args:
        frequencies: Frequency values (mm^-1).
        nps_values: Raw NPS values.
        degree: Polynomial degree (default 11, as iQMetrix).

    Returns:
        Fitted NPS values at the same frequency points.
    """
    if len(frequencies) < degree + 1:
        # Not enough points for fitting, return original
        return nps_values.copy()

    # Fit polynomial
    # Use weights to reduce influence of noisy high-frequency tail
    try:
        coeffs = np.polyfit(frequencies, nps_values, degree)
        nps_fit = np.polyval(coeffs, frequencies)

        # Ensure non-negative values (NPS is power, must be >= 0)
        nps_fit = np.maximum(nps_fit, 0)

        return nps_fit
    except (np.linalg.LinAlgError, ValueError):
        # Fitting failed, return original
        return nps_values.copy()


def calculate_nps_roi_positions(
    image: DicomImage,
    center: Optional[tuple[int, int]] = None,
    roi_size: Optional[int] = None,
    radius_pixels: Optional[float] = None,
    geometry: Optional[ROIGeometry] = None,
) -> list[NPSROIPosition]:
    """
    Calculate positions for 8 NPS ROIs in octagonal pattern.

    Sizes and offsets come from ROIGeometry (rules derived from the ANSM
    reference ROI files, relative to the INNER radius of the phantom):
    - side = 15 % of the inner diameter, rounded to 8 px, within 32-128 px
      (64 px on every ANSM reference series)
    - 4 cardinal ROIs (top, bottom, left, right) at ~45.5% of the radius
    - 4 diagonal ROIs (corners) at ~36.7% of the radius in each axis

    Args:
        image: DicomImage to analyze.
        center: (row, col) phantom center. Auto-detected if None.
        roi_size: Side length in pixels; overrides the rule when given.
        radius_pixels: Inner phantom radius in pixels. Detected if None.
        geometry: Frozen ROI geometry to reuse (only the centre is detected then).

    Returns:
        List of 8 NPSROIPosition objects.
    """
    if geometry is None:
        if center is None or radius_pixels is None:
            detected = detect_phantom(image, initial_center=center)
            if center is None:
                center = detected.center
            if radius_pixels is None:
                radius_pixels = detected.radius
        geometry = ROIGeometry.from_phantom(
            2 * radius_pixels, image.pixel_size_mm, image.rows, image.columns)
    elif center is None:
        center = detect_phantom(image).center

    center_row, center_col = center
    if roi_size is None:
        roi_size = geometry.nps_roi_size
    cardinal_distance = geometry.nps_cardinal_distance
    diagonal_offset = geometry.nps_diagonal_offset

    # Create 8 ROI positions in the same order as JSON files:
    # 1-4: Diagonal corners (top-left, bottom-right, bottom-left, top-right)
    # 5-8: Cardinal positions (top, bottom, left, right)
    rois = [
        # Diagonal corners
        NPSROIPosition(  # Top-left
            x=center_col - diagonal_offset,
            y=center_row - diagonal_offset,
            side_square=roi_size,
        ),
        NPSROIPosition(  # Bottom-right
            x=center_col + diagonal_offset,
            y=center_row + diagonal_offset,
            side_square=roi_size,
        ),
        NPSROIPosition(  # Bottom-left
            x=center_col - diagonal_offset,
            y=center_row + diagonal_offset,
            side_square=roi_size,
        ),
        NPSROIPosition(  # Top-right
            x=center_col + diagonal_offset,
            y=center_row - diagonal_offset,
            side_square=roi_size,
        ),
        # Cardinal positions
        NPSROIPosition(  # Top
            x=center_col,
            y=center_row - cardinal_distance,
            side_square=roi_size,
        ),
        NPSROIPosition(  # Bottom
            x=center_col,
            y=center_row + cardinal_distance,
            side_square=roi_size,
        ),
        NPSROIPosition(  # Left
            x=center_col - cardinal_distance,
            y=center_row,
            side_square=roi_size,
        ),
        NPSROIPosition(  # Right
            x=center_col + cardinal_distance,
            y=center_row,
            side_square=roi_size,
        ),
    ]

    return rois


def analyze_nps(
    series: DicomSeries,
    num_slices: int = 10,
    roi_size: Optional[int] = None,
    center: Optional[tuple[int, int]] = None,
    slice_range: Optional[tuple[int, int]] = None,
    roi_positions: Optional[list[NPSROIPosition]] = None,
    geometry: Optional[ROIGeometry] = None,
) -> NPSResult:
    """
    Perform NPS analysis on a series of slices using 8 ROIs.

    Args:
        series: DicomSeries containing water phantom images.
        num_slices: Number of slices to use (default 10 per ANSM). Ignored if slice_range provided.
        roi_size: Size of square ROI in pixels. Default: from the ROI geometry
            (15 % of the inner phantom diameter; 64 px on the ANSM series).
        center: Phantom center. Auto-detected from middle slice if None.
        slice_range: Optional (start, end) indices (0-based, inclusive). If None, uses central slices.
        roi_positions: Optional list of pre-defined ROI positions. If None, positions are
            auto-calculated based on phantom geometry.
        geometry: Frozen ROI geometry of the installation, reused as is (same
            sizes and offsets as the reference control, only the centre is detected).

    Returns:
        NPSResult with NPS data and metrics.

    Raises:
        ValueError: If series has insufficient slices.
    """
    if slice_range is not None:
        start_idx, end_idx = slice_range
        start_idx = max(0, start_idx)
        end_idx = min(series.num_images - 1, end_idx)
        slices = series.images[start_idx:end_idx + 1]
        actual_num_slices = len(slices)
        if actual_num_slices < 1:
            raise ValueError("Slice range results in no slices.")
    else:
        if series.num_images < num_slices:
            raise ValueError(
                f"NPS analysis requires at least {num_slices} slices, "
                f"but series only has {series.num_images}."
            )
        # Use central slices
        start_idx = (series.num_images - num_slices) // 2
        end_idx = start_idx + num_slices - 1
        slices = series.images[start_idx:start_idx + num_slices]
        actual_num_slices = num_slices

    # Get center from middle slice if not provided
    middle_slice = slices[len(slices) // 2]
    phantom = detect_phantom(middle_slice, initial_center=center)
    if center is None:
        center = phantom.center

    pixel_size = middle_slice.pixel_size_mm

    # Use provided ROI positions or calculate them
    if roi_positions is None:
        if geometry is None:
            geometry = ROIGeometry.from_phantom(
                phantom.diameter, pixel_size, middle_slice.rows, middle_slice.columns)
        roi_positions = calculate_nps_roi_positions(
            middle_slice, center, roi_size, geometry=geometry)
    else:
        geometry = None  # positions imposed by the caller: not our geometry

    # The ROI size is the one of the positions (given or computed)
    roi_size = roi_positions[0].side_square

    # Extract and process ROIs from all slices
    # Also collect statistics for uniformity checking
    nps_sum = None
    total_roi_count = 0
    roi_stats: list[tuple[int, int, float, float]] = []  # (slice_idx, roi_idx, mean, std)
    skipped_rois: set[int] = set()

    for slice_idx, img in enumerate(slices):
        for roi_idx, roi_pos in enumerate(roi_positions):
            # Extract ROI at this position
            roi = extract_roi_for_nps(img, (roi_pos.y, roi_pos.x), roi_size)

            # Skip if ROI is not the expected size (near image edges)
            if roi.shape[0] != roi_size or roi.shape[1] != roi_size:
                skipped_rois.add(roi_idx)
                continue

            # Collect statistics before detrending for uniformity check
            roi_mean = float(np.mean(roi))
            roi_std = float(np.std(roi))
            roi_stats.append((slice_idx, roi_idx, roi_mean, roi_std))

            roi_detrended = detrend_roi(roi)
            nps_2d, freq_x, freq_y = compute_nps_2d(roi_detrended, pixel_size)

            if nps_sum is None:
                nps_sum = nps_2d
            else:
                nps_sum += nps_2d
            total_roi_count += 1

    if nps_sum is None or total_roi_count == 0:
        raise ValueError("No valid ROIs could be processed.")

    # Check ROI uniformity - detect outliers
    roi_warnings: list[ROIUniformityWarning] = []
    if roi_stats:
        all_means = np.array([s[2] for s in roi_stats])
        all_stds = np.array([s[3] for s in roi_stats])
        overall_mean = np.mean(all_means)
        overall_std_of_means = np.std(all_means)
        median_std = np.median(all_stds)

        for slice_idx, roi_idx, roi_mean, roi_std in roi_stats:
            warnings_for_roi = []

            # Check if mean deviates significantly (>3 sigma AND >2 HU from overall mean)
            if overall_std_of_means > 0:
                z_score = abs(roi_mean - overall_mean) / overall_std_of_means
                if z_score > 3 and abs(roi_mean - overall_mean) > 2.0:
                    warnings_for_roi.append(
                        f"moyenne atypique ({roi_mean:.1f} HU vs {overall_mean:.1f} HU attendu)"
                    )

            # Check if std is unusually high (>2x median std)
            if median_std > 0 and roi_std > 2 * median_std:
                warnings_for_roi.append(
                    f"écart-type élevé ({roi_std:.1f} HU vs {median_std:.1f} HU médian)"
                )

            if warnings_for_roi:
                roi_warnings.append(ROIUniformityWarning(
                    roi_index=roi_idx,
                    slice_index=slice_idx,
                    mean_hu=roi_mean,
                    std_hu=roi_std,
                    message=f"ROI {roi_idx + 1}, coupe {slice_idx + 1}: {'; '.join(warnings_for_roi)}"
                ))

    # Noise: mean of the standard deviations of the ROIs (before detrending)
    noise = float(np.mean([s[3] for s in roi_stats]))

    # Average NPS across all ROIs and slices
    nps_avg = nps_sum / total_roi_count

    # Ring average, then the mean frequency on the raw radial spectrum
    nps_radial, freq_radial = radial_average(nps_avg, freq_x, freq_y, pixel_size)
    mean_frequency = mean_frequency_of(freq_radial, nps_radial)

    # Smoothed curve, for display and comparison with the reference fit only
    nps_radial_fit = fit_nps_polynomial(freq_radial, nps_radial, degree=11)

    # Compute summary metrics
    average_nps = np.mean(nps_radial)
    df = freq_radial[1] - freq_radial[0] if len(freq_radial) > 1 else 1.0
    total_noise_power = np.sum(nps_radial) * df

    # Build ROI configuration for export
    # Inner (water) diameter; the ANSM files give the nominal outer diameter
    phantom_diameter = phantom.diameter * pixel_size
    # Get slice positions in mm from DICOM slice_location
    slice_start_mm = slices[0].slice_location
    slice_end_mm = slices[-1].slice_location
    roi_config = NPSROIConfig(
        phantom_name=middle_slice.series_description or "",
        measurement_date=middle_slice.acquisition_date or middle_slice.study_date or "",
        dfov=middle_slice.reconstruction_diameter or (middle_slice.columns * pixel_size),
        pixel_size=pixel_size,
        width_in_pixel=middle_slice.columns,
        phantom_diameter=phantom_diameter,
        slice_start_mm=slice_start_mm,
        slice_end_mm=slice_end_mm,
        rois=roi_positions,
    )

    return NPSResult(
        nps_2d=nps_avg,
        frequencies_x=freq_x,
        frequencies_y=freq_y,
        nps_radial=nps_radial,
        nps_radial_fit=nps_radial_fit,
        frequencies_radial=freq_radial,
        mean_frequency=mean_frequency,
        average_nps=average_nps,
        total_noise_power=total_noise_power,
        num_slices=actual_num_slices,
        roi_size=roi_size,
        pixel_size_mm=pixel_size,
        roi_config=roi_config,
        roi_warnings=roi_warnings,
        skipped_rois=sorted(skipped_rois),
        geometry=geometry,
        noise=noise,
        noise_roi_count=len(roi_stats),
    )


def format_nps_results_text(result: NPSResult) -> str:
    """Format NPS results as human-readable text."""
    lines = [
        "═══════════════════════════════════════",
        "   SPECTRE DE PUISSANCE DU BRUIT (SPB)",
        "═══════════════════════════════════════",
        "",
        f"Nombre de coupes analysées: {result.num_slices}",
        f"Taille ROI: {result.roi_size} × {result.roi_size} pixels",
        f"Taille pixel: {result.pixel_size_mm:.3f} mm",
        "",
        "RÉSULTATS",
        f"  Bruit: {result.noise:.2f} HU (moyenne de {result.noise_roi_count} écarts-types)",
        f"  Fréquence moyenne: {result.mean_frequency:.3f} cycles/mm",
        f"  NPS moyen: {result.average_nps:.2f} HU²·mm²",
        f"  Puissance totale: {result.total_noise_power:.2f} HU²",
        "",
        "═══════════════════════════════════════",
    ]
    return "\n".join(lines)
