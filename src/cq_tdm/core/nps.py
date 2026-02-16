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
from scipy import integrate

from .dicom_loader import DicomImage, DicomSeries, detect_phantom_center, estimate_phantom_diameter


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
    nps_radial: np.ndarray  # Radially averaged NPS (raw)
    nps_radial_fit: np.ndarray  # Radially averaged NPS (11th order poly fit)
    frequencies_radial: np.ndarray  # Frequencies up to Nyquist

    # Summary metrics
    mean_frequency: float  # Mean frequency (centroid) of fitted NPS (cycles/mm)
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

    def to_dict(self) -> dict:
        """Convert to dictionary for reporting."""
        return {
            "mean_frequency": self.mean_frequency,
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
    Compute radially averaged 1D NPS using angular profile method.

    Matches iQMetrix reference implementation:
    - Extract radial profiles at 37 angles (0° to 360° in 10° steps)
    - Use bilinear interpolation along each profile
    - Average all profiles to get 1D NPS

    Args:
        nps_2d: 2D NPS matrix.
        freq_x: X frequency axis.
        freq_y: Y frequency axis.
        pixel_size_mm: Pixel size in mm for Nyquist calculation.
        num_bins: Number of output points (default: FFT_size/2 per iQMetrix).

    Returns:
        Tuple of (nps_radial, frequencies_radial).
    """
    from scipy import ndimage

    rows, cols = nps_2d.shape
    if rows != cols:
        raise ValueError(f"NPS array must be square, got {rows}x{cols}")
    fft_size = rows

    # Calculate Nyquist frequency
    nyquist = 1.0 / (2.0 * pixel_size_mm)

    # iQMetrix extends to 1.375 × Nyquist (covers diagonal of 2D frequency space)
    # Reference data consistently shows max_freq / Nyquist = 1.375 for all series
    freq_max = nyquist * 1.375

    # Number of bins: reference uses int(fft_size/2 * 1.375) + 1 = 45 for fft_size=64
    # This maintains consistent frequency resolution when extending beyond Nyquist
    if num_bins is None:
        num_bins = int(fft_size // 2 * 1.375) + 1

    # Frequency vector
    freq_r = np.linspace(0, freq_max, num_bins)

    # Center of 2D NPS (after fftshift, center is at (FFT_size/2+1, FFT_size/2+1) in 1-indexed MATLAB)
    # In 0-indexed Python: (FFT_size//2, FFT_size//2)
    center_row = fft_size // 2
    center_col = fft_size // 2

    # Profile length in pixels - extend to 1.375 × edge to match frequency range
    # This allows sampling beyond Nyquist into the corners of 2D frequency space
    r_pixels = int(fft_size // 2 * 1.375)

    # Angles for profile extraction (0 to 360 degrees in 10 degree steps)
    # iQMetrix: theta = 0:10:360 -> 37 angles
    theta_deg = np.arange(0, 361, 10)
    theta_rad = np.deg2rad(theta_deg)

    # Extract radial profiles at each angle using bilinear interpolation
    radial_profiles = []

    for theta in theta_rad:
        # Create points along the radial line from center to edge
        # r goes from 0 to r_pixels
        r_values = np.linspace(0, r_pixels, num_bins)

        # Calculate (row, col) coordinates along this line
        row_coords = center_row + r_values * np.sin(theta)
        col_coords = center_col + r_values * np.cos(theta)

        # Extract profile using bilinear interpolation (map_coordinates)
        # Note: map_coordinates uses (row, col) order
        coords = np.array([row_coords, col_coords])
        profile = ndimage.map_coordinates(nps_2d, coords, order=1, mode='constant', cval=0)
        radial_profiles.append(profile)

    # Average all radial profiles
    radial_profiles = np.array(radial_profiles)
    nps_r = np.mean(radial_profiles, axis=0)

    return nps_r, freq_r


def fit_nps_polynomial(
    frequencies: np.ndarray,
    nps_values: np.ndarray,
    degree: int = 11,
) -> np.ndarray:
    """
    Fit polynomial to 1D NPS curve.

    Uses 11th degree polynomial as per ANSM/iQMetrix reference method.

    Args:
        frequencies: Frequency values (mm^-1).
        nps_values: Raw NPS values.
        degree: Polynomial degree (default 11 per ANSM).

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
    roi_size: int = 64,
) -> list[NPSROIPosition]:
    """
    Calculate positions for 8 NPS ROIs in octagonal pattern.

    Based on analysis of ANSM reference data, ROIs are positioned:
    - 4 cardinal ROIs (top, bottom, left, right) at ~42% of phantom radius
    - 4 diagonal ROIs (corners) at ~34% of radius in each axis

    Args:
        image: DicomImage to analyze.
        center: (row, col) phantom center. Auto-detected if None.
        roi_size: Side length of square ROIs in pixels.

    Returns:
        List of 8 NPSROIPosition objects.
    """
    if center is None:
        center = detect_phantom_center(image)

    center_row, center_col = center

    # Estimate phantom diameter
    diameter_pixels = estimate_phantom_diameter(image, center)
    radius_pixels = diameter_pixels / 2

    # ROI positioning based on ANSM reference data analysis:
    # - Cardinal ROIs at ~42% of radius from center
    # - Diagonal ROIs at ~34% of radius in each axis direction
    cardinal_distance = int(radius_pixels * 0.42)
    diagonal_offset = int(radius_pixels * 0.34)

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
    roi_size: int = 64,
    center: Optional[tuple[int, int]] = None,
    slice_range: Optional[tuple[int, int]] = None,
    roi_positions: Optional[list[NPSROIPosition]] = None,
) -> NPSResult:
    """
    Perform NPS analysis on a series of slices using 8 ROIs.

    Args:
        series: DicomSeries containing water phantom images.
        num_slices: Number of slices to use (default 10 per ANSM). Ignored if slice_range provided.
        roi_size: Size of square ROI in pixels (default 64 per ANSM standard).
        center: Phantom center. Auto-detected from middle slice if None.
        slice_range: Optional (start, end) indices (0-based, inclusive). If None, uses central slices.
        roi_positions: Optional list of pre-defined ROI positions. If None, positions are
            auto-calculated based on phantom geometry.

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
    if center is None:
        center = detect_phantom_center(middle_slice)

    pixel_size = middle_slice.pixel_size_mm

    # Use provided ROI positions or calculate them
    if roi_positions is None:
        roi_positions = calculate_nps_roi_positions(middle_slice, center, roi_size)

    # Determine ROI size from positions (use first ROI's size, or default)
    if roi_positions:
        roi_size = roi_positions[0].side_square

    # Extract and process ROIs from all slices
    # Also collect statistics for uniformity checking
    nps_sum = None
    total_roi_count = 0
    roi_stats: list[tuple[int, int, float, float]] = []  # (slice_idx, roi_idx, mean, std)

    for slice_idx, img in enumerate(slices):
        for roi_idx, roi_pos in enumerate(roi_positions):
            # Extract ROI at this position
            roi = extract_roi_for_nps(img, (roi_pos.y, roi_pos.x), roi_size)

            # Skip if ROI is not the expected size (near image edges)
            if roi.shape[0] != roi_size or roi.shape[1] != roi_size:
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

    # Average NPS across all ROIs and slices
    nps_avg = nps_sum / total_roi_count

    # Compute radial average
    nps_radial, freq_radial = radial_average(nps_avg, freq_x, freq_y, pixel_size)

    # Apply 11th degree polynomial fit (per ANSM/iQMetrix method)
    nps_radial_fit = fit_nps_polynomial(freq_radial, nps_radial, degree=11)

    # Compute mean frequency (centroid of FITTED NPS curve)
    # f_mean = ∫ f * NPS(f) df / ∫ NPS(f) df
    # Use trapezoid integration for better accuracy
    total_power = integrate.trapezoid(nps_radial_fit, freq_radial)
    if total_power > 0:
        mean_frequency = integrate.trapezoid(freq_radial * nps_radial_fit, freq_radial) / total_power
    else:
        mean_frequency = 0.0

    # Compute summary metrics
    average_nps = np.mean(nps_radial)
    df = freq_radial[1] - freq_radial[0] if len(freq_radial) > 1 else 1.0
    total_noise_power = np.sum(nps_radial) * df

    # Build ROI configuration for export
    phantom_diameter = estimate_phantom_diameter(middle_slice, center) * pixel_size
    # Get slice positions in mm from DICOM slice_location
    slice_start_mm = slices[0].slice_location
    slice_end_mm = slices[-1].slice_location
    roi_config = NPSROIConfig(
        phantom_name=middle_slice.series_description or "",
        measurement_date=middle_slice.study_date or "",
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
        f"  Fréquence moyenne: {result.mean_frequency:.3f} cycles/mm",
        f"  NPS moyen: {result.average_nps:.2f} HU²·mm²",
        f"  Puissance totale: {result.total_noise_power:.2f} HU²",
        "",
        "═══════════════════════════════════════",
    ]
    return "\n".join(lines)
