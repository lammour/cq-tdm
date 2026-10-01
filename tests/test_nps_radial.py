"""Radial reduction of the 2D NPS and mean frequency, on synthetic spectra.

These tests need no reference images. They pin down the convention validated
against the ANSM image bank in test_nps_validation.py: ring k collects the
samples of the 2D spectrum whose distance to the zero frequency is in
[k, k + 1), and is assigned the frequency k / (N · pixel size).
"""

import numpy as np
import pytest

from cq_tdm.core.dicom_loader import DicomSeries
from cq_tdm.core.nps import (
    analyze_nps,
    compute_nps_2d,
    detrend_roi,
    mean_frequency_of,
    radial_average,
)

from .test_phantom_detection import make_phantom


def _radial(nps_2d: np.ndarray, pixel_size_mm: float = 0.5):
    n = nps_2d.shape[0]
    axis = np.fft.fftshift(np.fft.fftfreq(n, pixel_size_mm))
    return radial_average(nps_2d, axis, axis, pixel_size_mm)


@pytest.mark.parametrize("size", [32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120, 128])
def test_frequency_axis_is_exact_for_every_roi_size(size):
    """One ring is one sample of the 2D spectrum wide, whatever the ROI size."""
    pixel = 0.47
    _, freqs = _radial(np.ones((size, size)), pixel)
    assert len(freqs) == int(size // 2 * 1.375) + 1
    np.testing.assert_allclose(np.diff(freqs), 1 / (size * pixel), rtol=1e-12)
    assert freqs[0] == 0.0


def test_reference_extent_for_a_64_px_roi():
    """45 rings up to 1.375 × Nyquist, as in the iQMetrix-CT reference files."""
    pixel = 0.46875
    _, freqs = _radial(np.ones((64, 64)), pixel)
    assert len(freqs) == 45
    assert freqs[-1] == pytest.approx(1.375 * 0.5 / pixel)


def test_ring_k_holds_the_samples_between_k_and_k_plus_one():
    n = 64
    rows, cols = np.indices((n, n))
    distance = np.hypot(rows - n // 2, cols - n // 2)

    # A spectrum equal to floor(distance) must come back as 0, 1, 2, ...
    radial, _ = _radial(np.floor(distance))
    np.testing.assert_allclose(radial, np.arange(len(radial)))

    # The first ring is the zero frequency alone
    only_dc = np.zeros((n, n))
    only_dc[n // 2, n // 2] = 7.0
    radial, _ = _radial(only_dc)
    assert radial[0] == 7.0 and not radial[1:].any()

    # Ring 1: the 8 neighbours of the zero frequency (distances 1 and √2)
    neighbours = ((distance >= 1) & (distance < 2)).astype(float)
    assert neighbours.sum() == 8
    radial, _ = _radial(neighbours)
    assert radial[1] == 1.0 and radial[0] == 0.0 and not radial[2:].any()


def test_flat_spectrum_stays_flat_beyond_nyquist():
    """Past Nyquist only the corners hold data: averaged as they are, not with zeros."""
    radial, freqs = _radial(np.full((64, 64), 25.0), pixel_size_mm=0.5)
    assert freqs[-1] > 1.0  # Nyquist is 1.0 mm⁻¹ here
    np.testing.assert_allclose(radial, 25.0)


def test_radial_average_does_not_depend_on_orientation():
    rng = np.random.default_rng(3)
    spectrum = rng.random((64, 64))
    # Rotating about the zero frequency (index N // 2): drop the unpaired first
    # row and column, which have no counterpart on the other side
    spectrum[0, :] = spectrum[:, 0] = 0.0
    inner = spectrum[1:, 1:]
    rotated = np.zeros_like(spectrum)
    rotated[1:, 1:] = np.rot90(inner)
    np.testing.assert_allclose(_radial(rotated)[0], _radial(spectrum)[0])


def test_radial_average_rejects_a_non_square_spectrum():
    with pytest.raises(ValueError):
        radial_average(np.ones((64, 32)), np.zeros(32), np.zeros(64), 0.5)


def test_mean_frequency_is_the_centroid_of_the_raw_curve():
    freqs = np.linspace(0.0, 1.0, 101)
    triangle = np.where(freqs <= 0.5, freqs, 1.0 - freqs)  # symmetric about 0.5
    assert mean_frequency_of(freqs, triangle) == pytest.approx(0.5)
    assert mean_frequency_of(freqs, np.zeros_like(freqs)) == 0.0
    # A single line at 0.3 mm⁻¹
    line = np.zeros_like(freqs)
    line[30] = 4.0
    assert mean_frequency_of(freqs, line) == pytest.approx(0.3)


def test_white_noise_gives_a_flat_spectrum_of_the_right_level():
    """NPS of white noise is σ² · pixel area at every frequency (Parseval)."""
    rng = np.random.default_rng(0)
    sigma, pixel, size = 10.0, 0.5, 64
    total = np.zeros((size, size))
    count = 300
    for _ in range(count):
        nps_2d, fx, fy = compute_nps_2d(detrend_roi(rng.normal(0, sigma, (size, size))), pixel)
        total += nps_2d
    radial, freqs = radial_average(total / count, fx, fy, pixel)
    expected = sigma ** 2 * pixel ** 2
    # The first rings are lowered by the detrending; the rest is flat, corners included
    np.testing.assert_allclose(radial[4:], expected, rtol=0.08)
    assert radial[4:].mean() == pytest.approx(expected, rel=0.01)


def test_noise_is_the_mean_of_the_roi_standard_deviations():
    """8 ROIs on 10 slices: the mean of 80 standard deviations (ANSM 9.1.7.2)."""
    sigma = 25.0
    series = DicomSeries(images=[make_phantom(noise_sigma=sigma, seed=i) for i in range(10)])
    result = analyze_nps(series)
    assert result.noise_roi_count == 80

    half = result.roi_size // 2
    expected = np.mean([
        image.pixel_array[roi.y - half:roi.y + half, roi.x - half:roi.x + half].std()
        for image in series.images for roi in result.roi_config.rois
    ])
    assert result.noise == pytest.approx(expected)
    assert result.noise == pytest.approx(sigma, rel=0.01)

    # Fewer slices: fewer values, same quantity
    fewer = analyze_nps(series, slice_range=(0, 2))
    assert fewer.noise_roi_count == 24
    assert fewer.noise == pytest.approx(sigma, rel=0.02)


def test_analysis_reports_the_raw_centroid():
    series = DicomSeries(images=[make_phantom(seed=i) for i in range(10)])
    result = analyze_nps(series)
    assert result.mean_frequency == pytest.approx(
        mean_frequency_of(result.frequencies_radial, result.nps_radial))
    step = 1 / (result.roi_size * result.pixel_size_mm)
    np.testing.assert_allclose(np.diff(result.frequencies_radial), step, rtol=1e-12)
    # White noise in the phantom: the centroid sits in the middle of the range
    assert result.mean_frequency == pytest.approx(result.frequencies_radial[-1] / 2, rel=0.05)
