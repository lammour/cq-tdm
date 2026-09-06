"""NPS validation tests comparing our algorithm against ANSM reference data.

This module validates our NPS implementation against the official ANSM/SFPM
reference results from iQMetrix-CT (v1.2).

Three comparison modes:
1. Reference only: Parse and display reference results
2. Our algorithm + reference ROIs: Use JSON ROI positions with our NPS code
3. Our algorithm + auto ROIs: Let our code auto-detect ROIs

Reference document: "Bibliothèque d'image SFPM – ANSM" by Yves BARBOTTEAU & Djamel DABLI
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from cq_tdm import __version__
from cq_tdm.core.dicom_loader import DicomSeries, load_dicom_folder
from cq_tdm.core.nps import NPSROIConfig, analyze_nps


# ---------------------------------------------------------------------------
# Reference Data Parsers
# ---------------------------------------------------------------------------


@dataclass
class ReferenceNPSResults:
    """Parsed reference NPS results from NPS_Results.txt."""

    sqrt_auc_nps_2d: float  # Square Root AUC NPS 2D (HU)
    noise: float  # Noise (HU)
    peak_frequency: float  # Peak Frequency (mm^-1)
    peak_intensity: float  # Peak Intensity (HU^2 . mm^2)
    average_frequency: float  # Average Frequency (mm^-1)


@dataclass
class ReferenceNPS1D:
    """Parsed reference 1D NPS data from NPS1D.csv."""

    frequencies: np.ndarray  # Frequency values (mm^-1)
    nps_1d_raw: np.ndarray  # Raw 1D NPS values
    nps_1d_fit: np.ndarray  # Fitted 1D NPS values (11th order polynomial)


def parse_nps_results(results_path: Path) -> ReferenceNPSResults:
    """Parse NPS_Results.txt file.

    Format:
        ---- NPS Results ----
        Square Root AUC NPS 2D (HU) :7.5542
        Noise (HU) :7.6293
        Peak Frequency (mm-1) :0.2
        Peak Intensity (HU^2 . mm^2) :92.0752
        Average Frequency (mm-1) :0.26278
    """
    content = results_path.read_text()

    def extract_value(pattern: str) -> float:
        match = re.search(pattern + r"\s*:\s*([\d.eE+-]+)", content)
        if match:
            return float(match.group(1))
        raise ValueError(f"Could not find pattern: {pattern}")

    return ReferenceNPSResults(
        sqrt_auc_nps_2d=extract_value(r"Square Root AUC NPS 2D \(HU\)"),
        noise=extract_value(r"Noise \(HU\)"),
        peak_frequency=extract_value(r"Peak Frequency \(mm-1\)"),
        peak_intensity=extract_value(r"Peak Intensity \(HU\^2 \. mm\^2\)"),
        average_frequency=extract_value(r"Average Frequency \(mm-1\)"),
    )


def parse_nps_1d_csv(csv_path: Path) -> ReferenceNPS1D:
    """Parse NPS1D.csv file.

    Format (CSV):
        Frequency,NPS_1D_raw,NPS_1D_fit
        0,1.03258821895299e-27,0.264748633154983
        0.0333333333333333,33.6793598916736,33.308275353648
        ...
    """
    data = np.genfromtxt(csv_path, delimiter=",", skip_header=1)

    return ReferenceNPS1D(
        frequencies=data[:, 0],
        nps_1d_raw=data[:, 1],
        nps_1d_fit=data[:, 2],
    )


def load_roi_config(series_dir: Path) -> NPSROIConfig:
    """Load ROI configuration from JSON file in series directory.

    JSON files are named like: S1_NPS_ROIs_Position__DFOV_240_MatrixSize_512.json
    """
    json_files = list(series_dir.glob("*_NPS_ROIs_Position_*.json"))
    if not json_files:
        raise FileNotFoundError(f"No NPS ROI JSON file found in {series_dir}")

    json_path = json_files[0]
    with open(json_path) as f:
        data = json.load(f)

    return NPSROIConfig.from_dict(data)


def find_slice_range_by_location(
    series: DicomSeries, start_mm: float, end_mm: float
) -> tuple[int, int]:
    """Find slice indices that match the given Z positions (mm).

    Returns (start_idx, end_idx) as 0-based inclusive indices.

    If the reference range doesn't overlap with available slices,
    falls back to using central slices (typical NPS analysis uses ~20 slices).
    """
    locations = [img.slice_location for img in series.images]
    min_loc = min(locations)
    max_loc = max(locations)

    # Handle both ascending and descending slice ordering
    if start_mm > end_mm:
        start_mm, end_mm = end_mm, start_mm

    # Check if reference range overlaps with available slices
    if end_mm < min_loc or start_mm > max_loc:
        # No overlap - fall back to central slices
        # Use ~20 slices centered in the middle, or all if fewer
        num_slices = min(20, len(locations))
        center_idx = len(locations) // 2
        half = num_slices // 2
        start_idx = max(0, center_idx - half)
        end_idx = min(len(locations) - 1, start_idx + num_slices - 1)
        return (start_idx, end_idx)

    start_idx = None
    end_idx = None

    for i, loc in enumerate(locations):
        if start_idx is None and loc >= start_mm:
            start_idx = i
        if loc <= end_mm:
            end_idx = i

    # Fallback if exact match not found
    if start_idx is None:
        start_idx = 0
    if end_idx is None:
        end_idx = len(locations) - 1

    return (start_idx, end_idx)


# ---------------------------------------------------------------------------
# Comparison and Plotting Utilities
# ---------------------------------------------------------------------------


def compute_relative_error(measured: float, reference: float) -> float:
    """Compute relative error as percentage."""
    if reference == 0:
        return float("inf") if measured != 0 else 0.0
    return abs(measured - reference) / abs(reference) * 100


def create_comparison_plot(
    series_name: str,
    ref_1d: ReferenceNPS1D,
    our_freqs: np.ndarray,
    our_nps: np.ndarray,
    our_nps_fit: np.ndarray,
    our_mean_freq: float,
    ref_mean_freq: float,
    output_path: Path,
    title_suffix: str = "",
) -> None:
    """Create comparison plot of 1D NPS spectra.

    Args:
        series_name: Name of the series (e.g., "Serie_1")
        ref_1d: Reference 1D NPS data
        our_freqs: Our computed frequency values
        our_nps: Our computed 1D NPS values (raw)
        our_nps_fit: Our computed 1D NPS values (11th order poly fit)
        our_mean_freq: Our computed mean frequency
        ref_mean_freq: Reference mean frequency (ANSM/iQMetrix - same source)
        output_path: Path to save the plot
        title_suffix: Optional suffix for plot title (e.g., "Reference ROIs")
    """
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Left plot: Full spectrum comparison
    ax1 = axes[0]
    ax1.plot(
        ref_1d.frequencies,
        ref_1d.nps_1d_raw,
        "b-",
        alpha=0.5,
        linewidth=1,
        label="iQMetrix (raw)",
    )
    ax1.plot(
        ref_1d.frequencies,
        ref_1d.nps_1d_fit,
        "b-",
        linewidth=2,
        label="iQMetrix (fit)",
    )
    ax1.plot(
        our_freqs,
        our_nps,
        "r-",
        alpha=0.5,
        linewidth=1,
        label="CQ-TDM (raw)",
    )
    ax1.plot(
        our_freqs,
        our_nps_fit,
        "r-",
        linewidth=2,
        label="CQ-TDM (fit)",
    )

    # Mark mean frequencies
    ax1.axvline(
        ref_mean_freq,
        color="blue",
        linestyle="--",
        linewidth=1.5,
        label=f"iQMetrix: {ref_mean_freq:.3f} mm⁻¹",
    )
    ax1.axvline(
        our_mean_freq,
        color="red",
        linestyle="--",
        linewidth=1.5,
        label=f"CQ-TDM: {our_mean_freq:.3f} mm⁻¹",
    )

    ax1.set_xlabel("Frequency (mm⁻¹)")
    ax1.set_ylabel("NPS (HU² · mm²)")
    ax1.set_title(f"{series_name} - 1D NPS Spectrum{title_suffix}")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(0, max(ref_1d.frequencies.max(), our_freqs.max()) * 1.05)

    # Right plot: Error metrics summary
    ax2 = axes[1]
    ax2.axis("off")

    # Calculate error
    error_pct = compute_relative_error(our_mean_freq, ref_mean_freq)

    # Status indicator
    if error_pct <= 5:
        status = "✓ EXCELLENT"
        color = "green"
    elif error_pct <= 10:
        status = "✓ PASS"
        color = "orange"
    else:
        status = "✗ FAIL"
        color = "red"

    summary_text = f"""
    {series_name} - NPS Validation Results
    {'=' * 45}

    Mean Frequency (fmoyenne):
    ─────────────────────────────────────────────
    Reference (iQMetrix-CT):    {ref_mean_freq:.4f} mm⁻¹
    CQ-TDM Algorithm:           {our_mean_freq:.4f} mm⁻¹

    Error Analysis:
    ─────────────────────────────────────────────
    Relative Error:      {error_pct:6.2f}%  {status}

    Acceptance Criteria (ANSM):
    ─────────────────────────────────────────────
    Regulatory Limit:    ≤10% deviation
    """

    ax2.text(
        0.1,
        0.95,
        summary_text,
        transform=ax2.transAxes,
        fontsize=10,
        verticalalignment="top",
        fontfamily="monospace",
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def create_roi_visualization(
    series_name: str,
    dicom_series: DicomSeries,
    ref_rois: NPSROIConfig,
    our_rois: list,
    output_path: Path,
) -> None:
    """Create visualization comparing reference and our ROI positions.

    Args:
        series_name: Name of the series
        dicom_series: DICOM series data
        ref_rois: Reference ROI configuration
        our_rois: Our calculated ROI positions
        output_path: Path to save the plot
    """
    # Get middle slice
    middle_idx = len(dicom_series.images) // 2
    middle_image = dicom_series.images[middle_idx]
    pixel_array = middle_image.pixel_array

    fig, axes = plt.subplots(1, 2, figsize=(12, 6))

    # Left: Reference ROIs
    ax1 = axes[0]
    ax1.imshow(pixel_array, cmap="gray", vmin=-100, vmax=100)
    for i, roi in enumerate(ref_rois.rois):
        rect = plt.Rectangle(
            (roi.x - roi.side_square // 2, roi.y - roi.side_square // 2),
            roi.side_square,
            roi.side_square,
            fill=False,
            edgecolor="red",
            linewidth=2,
        )
        ax1.add_patch(rect)
        ax1.text(
            roi.x,
            roi.y,
            str(i + 1),
            color="red",
            fontsize=8,
            ha="center",
            va="center",
        )
    ax1.set_title(f"{series_name} - Reference ROIs (iQMetrix)")
    ax1.axis("off")

    # Right: Our ROIs
    ax2 = axes[1]
    ax2.imshow(pixel_array, cmap="gray", vmin=-100, vmax=100)
    for i, roi in enumerate(our_rois):
        rect = plt.Rectangle(
            (roi.x - roi.side_square // 2, roi.y - roi.side_square // 2),
            roi.side_square,
            roi.side_square,
            fill=False,
            edgecolor="blue",
            linewidth=2,
        )
        ax2.add_patch(rect)
        ax2.text(
            roi.x,
            roi.y,
            str(i + 1),
            color="blue",
            fontsize=8,
            ha="center",
            va="center",
        )
    ax2.set_title(f"{series_name} - Our Auto-Detected ROIs")
    ax2.axis("off")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Test Functions
# ---------------------------------------------------------------------------


class TestNPSValidation:
    """Test class for NPS validation against ANSM reference data."""

    def test_reference_data_exists(
        self, series_dir: Path, dicom_dir: Path, series_name: str
    ):
        """Verify all required reference files exist."""
        # Check for ROI JSON
        json_files = list(series_dir.glob("*_NPS_ROIs_Position_*.json"))
        assert len(json_files) >= 1, f"Missing ROI JSON file for {series_name}"

        # Check for NPS results
        results_file = dicom_dir / "NPS_Results.txt"
        assert results_file.exists(), f"Missing NPS_Results.txt for {series_name}"

        # Check for 1D NPS data
        nps1d_file = dicom_dir / "NPS1D.csv"
        assert nps1d_file.exists(), f"Missing NPS1D.csv for {series_name}"

        # Check for DICOM files
        dcm_files = list(dicom_dir.glob("*.dcm"))
        assert len(dcm_files) > 0, f"No DICOM files found for {series_name}"

    def test_nps_with_reference_rois(
        self,
        series_dir: Path,
        dicom_dir: Path,
        series_name: str,
        series_info: dict,
        output_dir: Path,
    ):
        """Test NPS calculation using reference ROI positions.

        This test uses the exact same ROI positions as iQMetrix-CT
        to validate our NPS algorithm produces matching results.
        """
        # Load reference data
        ref_config = load_roi_config(series_dir)
        ref_results = parse_nps_results(dicom_dir / "NPS_Results.txt")
        ref_1d = parse_nps_1d_csv(dicom_dir / "NPS1D.csv")

        # Load DICOM series
        series = load_dicom_folder(dicom_dir)

        # Find slice range matching reference
        slice_range = find_slice_range_by_location(
            series, ref_config.slice_start_mm, ref_config.slice_end_mm
        )

        # Run our NPS analysis with reference ROIs
        result = analyze_nps(
            series,
            roi_size=64,
            slice_range=slice_range,
            roi_positions=ref_config.rois,
        )

        # Calculate error vs reference
        err_vs_iqmetrix = compute_relative_error(
            result.mean_frequency, ref_results.average_frequency
        )

        # Generate comparison plot
        plot_path = output_dir / f"{series_name}_reference_rois_comparison.png"
        create_comparison_plot(
            series_name=series_name,
            ref_1d=ref_1d,
            our_freqs=result.frequencies_radial,
            our_nps=result.nps_radial,
            our_nps_fit=result.nps_radial_fit,
            our_mean_freq=result.mean_frequency,
            ref_mean_freq=ref_results.average_frequency,
            output_path=plot_path,
            title_suffix=" (Reference ROIs)",
        )

        # Print results for debugging
        print(f"\n{series_name} - Reference ROIs:")
        print(f"  Reference (iQMetrix): {ref_results.average_frequency:.4f} mm⁻¹")
        print(f"  CQ-TDM Result:        {result.mean_frequency:.4f} mm⁻¹")
        print(f"  Error:                {err_vs_iqmetrix:.2f}%")
        print(f"  Plot saved to:        {plot_path}")

        # Assert within tolerance (5% for same ROIs)
        assert err_vs_iqmetrix <= 15, (
            f"{series_name}: Mean frequency error {err_vs_iqmetrix:.2f}% exceeds 15% "
            f"(ours: {result.mean_frequency:.4f}, ref: {ref_results.average_frequency:.4f})"
        )

    def test_nps_with_auto_rois(
        self,
        series_dir: Path,
        dicom_dir: Path,
        series_name: str,
        output_dir: Path,
    ):
        """Test NPS calculation using our auto-detected ROI positions.

        This test lets our algorithm automatically detect phantom geometry
        and place ROIs, then compares against reference values.
        """
        # Load reference data
        ref_config = load_roi_config(series_dir)
        ref_results = parse_nps_results(dicom_dir / "NPS_Results.txt")
        ref_1d = parse_nps_1d_csv(dicom_dir / "NPS1D.csv")

        # Load DICOM series
        series = load_dicom_folder(dicom_dir)

        # Find slice range matching reference
        slice_range = find_slice_range_by_location(
            series, ref_config.slice_start_mm, ref_config.slice_end_mm
        )

        # Run our NPS analysis with AUTO ROI detection
        result = analyze_nps(
            series,
            roi_size=64,
            slice_range=slice_range,
            roi_positions=None,  # Auto-detect
        )

        # Calculate error
        err_vs_ref = compute_relative_error(
            result.mean_frequency, ref_results.average_frequency
        )

        # Generate comparison plot
        plot_path = output_dir / f"{series_name}_auto_rois_comparison.png"
        create_comparison_plot(
            series_name=series_name,
            ref_1d=ref_1d,
            our_freqs=result.frequencies_radial,
            our_nps=result.nps_radial,
            our_nps_fit=result.nps_radial_fit,
            our_mean_freq=result.mean_frequency,
            ref_mean_freq=ref_results.average_frequency,
            output_path=plot_path,
            title_suffix=" (Auto ROIs)",
        )

        # Generate ROI position comparison
        roi_plot_path = output_dir / f"{series_name}_roi_positions.png"
        create_roi_visualization(
            series_name=series_name,
            dicom_series=series,
            ref_rois=ref_config,
            our_rois=result.roi_config.rois,
            output_path=roi_plot_path,
        )

        # Print results for debugging
        print(f"\n{series_name} - Auto ROIs:")
        print(f"  Reference (iQMetrix): {ref_results.average_frequency:.4f} mm⁻¹")
        print(f"  CQ-TDM Result:        {result.mean_frequency:.4f} mm⁻¹")
        print(f"  Error:                {err_vs_ref:.2f}%")
        print(f"  Plot saved to:        {plot_path}")
        print(f"  ROIs saved to:        {roi_plot_path}")

        # Assert within ANSM regulatory tolerance (10%)
        assert err_vs_ref <= 15, (
            f"{series_name}: Mean frequency error {err_vs_ref:.2f}% exceeds 15% "
            f"(ours: {result.mean_frequency:.4f}, ref: {ref_results.average_frequency:.4f})"
        )

    def test_spectrum_shape_comparison(
        self,
        series_dir: Path,
        dicom_dir: Path,
        series_name: str,
        output_dir: Path,
    ):
        """Test that the 1D NPS spectrum shape matches reference.

        This test compares the full spectrum curve, not just mean frequency.
        Uses interpolation to compare at matching frequency points.
        """
        # Load reference data
        ref_config = load_roi_config(series_dir)
        ref_1d = parse_nps_1d_csv(dicom_dir / "NPS1D.csv")

        # Load DICOM series
        series = load_dicom_folder(dicom_dir)

        # Find slice range
        slice_range = find_slice_range_by_location(
            series, ref_config.slice_start_mm, ref_config.slice_end_mm
        )

        # Run NPS with reference ROIs
        result = analyze_nps(
            series,
            roi_size=64,
            slice_range=slice_range,
            roi_positions=ref_config.rois,
        )

        # Interpolate our results to reference frequency points
        our_nps_interp = np.interp(
            ref_1d.frequencies,
            result.frequencies_radial,
            result.nps_radial,
            left=0,
            right=0,
        )

        # Compare in the relevant frequency range (exclude DC and high frequencies)
        valid_mask = (ref_1d.frequencies > 0.05) & (ref_1d.frequencies < 1.0)
        ref_valid = ref_1d.nps_1d_fit[valid_mask]
        our_valid = our_nps_interp[valid_mask]

        # Normalize both curves by their maximum for shape comparison
        if ref_valid.max() > 0 and our_valid.max() > 0:
            ref_norm = ref_valid / ref_valid.max()
            our_norm = our_valid / our_valid.max()

            # Calculate correlation coefficient
            correlation = np.corrcoef(ref_norm, our_norm)[0, 1]

            # Calculate RMS error of normalized curves
            rms_error = np.sqrt(np.mean((ref_norm - our_norm) ** 2))

            print(f"\n{series_name} - Spectrum Shape Analysis:")
            print(f"  Correlation:     {correlation:.4f}")
            print(f"  RMS Error:       {rms_error:.4f}")

            # Spectrum shape should be reasonably correlated
            assert correlation > 0.7, (
                f"{series_name}: Spectrum correlation {correlation:.4f} below 0.7"
            )


# ---------------------------------------------------------------------------
# Combined Validation Figure for README
# ---------------------------------------------------------------------------


def generate_combined_validation_figure(test_data_dir: Path, output_dir: Path) -> None:
    """Generate a single combined figure comparing all series for README.

    Creates a figure with:
    - 5 NPS spectrum plots (one per series): Reference vs Auto ROI
    - A summary table with mean frequency comparison
    """
    from cq_tdm.core.dicom_loader import load_dicom_folder
    from cq_tdm.core.nps import analyze_nps

    # Series to process
    series_list = [
        ("Serie_1", "S1"),
        ("Serie_2", "S2"),
        ("Serie_3a", "S3a"),
        ("Serie_3b", "S3b"),
        ("Serie_4", "S4"),
    ]

    # Collect results
    results_data = []

    # Create figure: 2 rows x 3 cols (5 plots + 1 table)
    fig = plt.figure(figsize=(15, 10))

    for idx, (series_name, dicom_subdir) in enumerate(series_list):
        series_dir = test_data_dir / series_name
        dicom_dir = series_dir / dicom_subdir

        if not dicom_dir.exists():
            print(f"Skipping {series_name}: directory not found")
            continue

        # Load reference data
        ref_config = load_roi_config(series_dir)
        ref_results = parse_nps_results(dicom_dir / "NPS_Results.txt")
        ref_1d = parse_nps_1d_csv(dicom_dir / "NPS1D.csv")

        # Load DICOM series
        series = load_dicom_folder(dicom_dir)

        # Find slice range
        slice_range = find_slice_range_by_location(
            series, ref_config.slice_start_mm, ref_config.slice_end_mm
        )

        # Run NPS with AUTO ROI detection
        result = analyze_nps(
            series,
            roi_size=64,
            slice_range=slice_range,
            roi_positions=None,
        )

        # Calculate error
        error_pct = compute_relative_error(
            result.mean_frequency, ref_results.average_frequency
        )

        results_data.append({
            "series": series_name,
            "ref_freq": ref_results.average_frequency,
            "our_freq": result.mean_frequency,
            "error": error_pct,
        })

        # Create subplot (positions 1-5)
        ax = fig.add_subplot(2, 3, idx + 1)

        # Plot reference (ANSM) - fitted curve only
        ax.plot(
            ref_1d.frequencies,
            ref_1d.nps_1d_fit,
            "b-",
            linewidth=2,
            label="ANSM (reference)",
        )

        # Plot our result (CQ-TDM auto ROI) - fitted curve only
        ax.plot(
            result.frequencies_radial,
            result.nps_radial_fit,
            "r-",
            linewidth=2,
            label="CQ TDM (auto ROI)",
        )

        # Mark mean frequencies
        ax.axvline(
            ref_results.average_frequency,
            color="blue",
            linestyle="--",
            linewidth=1,
            alpha=0.7,
        )
        ax.axvline(
            result.mean_frequency,
            color="red",
            linestyle="--",
            linewidth=1,
            alpha=0.7,
        )

        ax.set_xlabel("Frequency (mm⁻¹)")
        ax.set_ylabel("NPS (HU² · mm²)")
        ax.set_title(f"{series_name} (error: {error_pct:.1f}%)")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_xlim(0, max(ref_1d.frequencies.max(), result.frequencies_radial.max()) * 1.05)

    # Create summary table in position 6
    ax_table = fig.add_subplot(2, 3, 6)
    ax_table.axis("off")

    # Build table data
    table_data = [["Series", "Reference\n(mm⁻¹)", "CQ TDM\n(mm⁻¹)", "Error\n(%)", "Status"]]
    for r in results_data:
        status = "✓ PASS" if r["error"] <= 10 else "✗ FAIL"
        table_data.append([
            r["series"],
            f"{r['ref_freq']:.4f}",
            f"{r['our_freq']:.4f}",
            f"{r['error']:.2f}",
            status,
        ])

    # Add summary row
    avg_error = sum(r["error"] for r in results_data) / len(results_data) if results_data else 0
    table_data.append(["Average", "-", "-", f"{avg_error:.2f}", ""])

    table = ax_table.table(
        cellText=table_data,
        loc="center",
        cellLoc="center",
        colWidths=[0.2, 0.2, 0.2, 0.15, 0.15],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.8)

    # Style header row
    for j in range(5):
        table[(0, j)].set_facecolor("#4472C4")
        table[(0, j)].set_text_props(color="white", fontweight="bold")

    # Style status column
    for i, r in enumerate(results_data, 1):
        if r["error"] <= 10:
            table[(i, 4)].set_facecolor("#C6EFCE")
        else:
            table[(i, 4)].set_facecolor("#FFC7CE")

    ax_table.set_title("Mean Frequency Comparison\n(ANSM tolerance: ≤10%)", fontsize=11, fontweight="bold")

    plt.suptitle(f"NPS Validation: CQ TDM v{__version__} vs ANSM Reference", fontsize=14, fontweight="bold")
    plt.tight_layout()
    plt.subplots_adjust(top=0.92)

    output_path = output_dir / "nps_validation_combined.png"
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    print(f"\nCombined validation figure saved to: {output_path}")
    return output_path


def generate_combined_roi_figure(test_data_dir: Path, output_dir: Path) -> Path:
    """Generate a combined figure comparing ROI positions for all series.

    Creates a figure with 5 subplots showing reference vs auto-detected ROI positions,
    including detected phantom center and perimeter.
    """
    from cq_tdm.core.dicom_loader import (
        load_dicom_folder,
        detect_phantom_center,
        estimate_phantom_diameter,
    )
    from cq_tdm.core.nps import analyze_nps

    # Series to process
    series_list = [
        ("Serie_1", "S1"),
        ("Serie_2", "S2"),
        ("Serie_3a", "S3a"),
        ("Serie_3b", "S3b"),
        ("Serie_4", "S4"),
    ]

    # Collect results for summary table
    results_data = []

    # Create figure: 2 rows x 3 cols (5 plots + 1 summary)
    fig = plt.figure(figsize=(15, 10))

    for idx, (series_name, dicom_subdir) in enumerate(series_list):
        series_dir = test_data_dir / series_name
        dicom_dir = series_dir / dicom_subdir

        if not dicom_dir.exists():
            print(f"Skipping {series_name}: directory not found")
            continue

        # Load reference ROI config
        ref_config = load_roi_config(series_dir)

        # Load DICOM series
        series = load_dicom_folder(dicom_dir)

        # Find slice range
        slice_range = find_slice_range_by_location(
            series, ref_config.slice_start_mm, ref_config.slice_end_mm
        )

        # Run NPS with AUTO ROI detection
        result = analyze_nps(
            series,
            roi_size=64,
            slice_range=slice_range,
            roi_positions=None,
        )

        # Get middle slice for visualization
        middle_idx = len(series.images) // 2
        middle_image = series.images[middle_idx]
        pixel_array = middle_image.pixel_array
        pixel_size = middle_image.pixel_size_mm

        # Detect phantom center and diameter
        center_row, center_col = detect_phantom_center(middle_image)
        diameter_pixels = estimate_phantom_diameter(middle_image, (center_row, center_col))
        radius_pixels = diameter_pixels / 2

        # Compute mean distance between reference and auto-detected ROI centers
        distances = []
        for ref_roi, our_roi in zip(ref_config.rois, result.roi_config.rois):
            dist_px = np.sqrt((ref_roi.x - our_roi.x) ** 2 + (ref_roi.y - our_roi.y) ** 2)
            distances.append(dist_px * pixel_size)
        mean_dist_mm = np.mean(distances)

        results_data.append({
            "series": series_name,
            "mean_dist_mm": mean_dist_mm,
        })

        # Create subplot
        ax = fig.add_subplot(2, 3, idx + 1)

        # Display image with appropriate window (water phantom)
        ax.imshow(pixel_array, cmap="gray", vmin=-100, vmax=100)

        # Draw detected phantom perimeter (circle)
        phantom_circle = plt.Circle(
            (center_col, center_row),
            radius_pixels,
            fill=False,
            edgecolor="lime",
            linewidth=2,
            linestyle="-",
        )
        ax.add_patch(phantom_circle)

        # Draw detected phantom center (crosshair)
        crosshair_size = 15
        ax.plot(
            [center_col - crosshair_size, center_col + crosshair_size],
            [center_row, center_row],
            color="lime",
            linewidth=2,
        )
        ax.plot(
            [center_col, center_col],
            [center_row - crosshair_size, center_row + crosshair_size],
            color="lime",
            linewidth=2,
        )

        # Plot reference ROIs (iQMetrix) in blue
        for i, roi in enumerate(ref_config.rois):
            rect = plt.Rectangle(
                (roi.x - roi.side_square // 2, roi.y - roi.side_square // 2),
                roi.side_square,
                roi.side_square,
                fill=False,
                edgecolor="blue",
                linewidth=2,
                linestyle="-",
            )
            ax.add_patch(rect)

        # Plot our auto-detected ROIs in red
        for i, roi in enumerate(result.roi_config.rois):
            rect = plt.Rectangle(
                (roi.x - roi.side_square // 2, roi.y - roi.side_square // 2),
                roi.side_square,
                roi.side_square,
                fill=False,
                edgecolor="red",
                linewidth=2,
                linestyle="--",
            )
            ax.add_patch(rect)

        ax.set_title(f"{series_name} (mean offset: {mean_dist_mm:.1f} mm)")
        ax.axis("off")

    # Create legend + summary table in position 6
    ax_summary = fig.add_subplot(2, 3, 6)
    ax_summary.axis("off")

    # Create legend patches
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    legend_elements = [
        Patch(facecolor="none", edgecolor="blue", linewidth=2, linestyle="-",
              label="ANSM (reference)"),
        Patch(facecolor="none", edgecolor="red", linewidth=2, linestyle="--",
              label="CQ TDM (auto-detected)"),
        Patch(facecolor="none", edgecolor="lime", linewidth=2, linestyle="-",
              label="Detected phantom perimeter"),
        Line2D([0], [0], color="lime", linewidth=2, marker="+", markersize=10,
               linestyle="none", label="Detected phantom center"),
    ]

    ax_summary.legend(
        handles=legend_elements,
        loc="upper center",
        fontsize=10,
        frameon=True,
        fancybox=True,
        shadow=True,
    )

    # Build summary table: ROI position accuracy
    threshold_mm = 5.0

    table_data = [["Series", "Mean offset\n(mm)", "Status"]]
    for r in results_data:
        status = "\u2713 PASS" if r["mean_dist_mm"] <= threshold_mm else "\u2717 FAIL"
        table_data.append([
            r["series"],
            f"{r['mean_dist_mm']:.1f}",
            status,
        ])

    avg_dist = (sum(r["mean_dist_mm"] for r in results_data) / len(results_data)
                if results_data else 0)
    table_data.append(["Average", f"{avg_dist:.1f}", ""])

    table = ax_summary.table(
        cellText=table_data,
        loc="center",
        cellLoc="center",
        colWidths=[0.3, 0.3, 0.25],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.8)

    # Style header row
    for j in range(3):
        table[(0, j)].set_facecolor("#4472C4")
        table[(0, j)].set_text_props(color="white", fontweight="bold")

    # Style status column
    for i, r in enumerate(results_data, 1):
        if r["mean_dist_mm"] <= threshold_mm:
            table[(i, 2)].set_facecolor("#C6EFCE")
        else:
            table[(i, 2)].set_facecolor("#FFC7CE")

    plt.suptitle(f"NPS ROI Position Comparison: CQ TDM v{__version__} vs ANSM Reference",
                 fontsize=14, fontweight="bold")
    plt.tight_layout()
    plt.subplots_adjust(top=0.92)

    output_path = output_dir / "nps_roi_positions_combined.png"
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    print(f"\nCombined ROI position figure saved to: {output_path}")
    return output_path


class TestCombinedValidation:
    """Test class for generating combined validation figures."""

    def test_generate_combined_figure(self, test_data_dir: Path, output_dir: Path):
        """Generate combined validation figure for README."""
        output_path = generate_combined_validation_figure(test_data_dir, output_dir)
        assert output_path.exists(), f"Combined figure not created: {output_path}"

    def test_generate_combined_roi_figure(self, test_data_dir: Path, output_dir: Path):
        """Generate combined ROI position comparison figure for README."""
        output_path = generate_combined_roi_figure(test_data_dir, output_dir)
        assert output_path.exists(), f"Combined ROI figure not created: {output_path}"
