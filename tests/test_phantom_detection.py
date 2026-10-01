"""Phantom geometry detection: centre and INNER radius of the water disc.

The ANSM decision places the peripheral ROIs 10-15 mm from the inner wall of
the phantom, so the detected radius must be the water/wall boundary, not the
outer edge of the wall.
"""

import numpy as np
import pytest

from cq_tdm.core.dicom_loader import DicomImage, detect_phantom, load_dicom_folder
from cq_tdm.core.water_phantom import calculate_rois

from .conftest import SERIES_INFO, TEST_DATA_DIR


# ---------------------------------------------------------------------------
# Synthetic phantom
# ---------------------------------------------------------------------------

PIXEL_MM = 0.5
WATER_RADIUS = 200.0  # px, inner wall
WALL_PX = 8  # wall thickness
WALL_HU = 120.0
AIR_HU = -1000.0


def make_phantom(
    center=(256.0, 256.0),
    noise_sigma=25.0,
    bubble_radius=0,
    table=False,
    seed=0,
) -> DicomImage:
    """512x512 HU image: noisy water disc, wall, air, optional bubble and table."""
    rng = np.random.default_rng(seed)
    rows = cols = 512
    y, x = np.ogrid[:rows, :cols]
    dist = np.hypot(y - center[0], x - center[1])
    image = np.full((rows, cols), AIR_HU)
    image[dist <= WATER_RADIUS + WALL_PX] = WALL_HU
    water = dist <= WATER_RADIUS
    image[water] = rng.normal(0.0, noise_sigma, size=int(water.sum()))
    if bubble_radius:
        # Air bubble at 12 o'clock, touching the inner wall
        b_row = center[0] - WATER_RADIUS + bubble_radius
        bubble = np.hypot(y - b_row, x - center[1]) <= bubble_radius
        image[bubble & water] = AIR_HU
    if table:
        # Water-like band under the phantom, spanning the image width
        image[500:510, :] = rng.normal(20.0, 10.0, size=(10, cols))
    return DicomImage(
        pixel_array=image,
        rows=rows,
        columns=cols,
        pixel_spacing=(PIXEL_MM, PIXEL_MM),
        reconstruction_diameter=rows * PIXEL_MM,
    )


class TestSyntheticPhantom:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"center": (266.0, 249.0)},  # off-centre phantom
            {"bubble_radius": 8},
            {"table": True},
            {"center": (270.0, 240.0), "bubble_radius": 8, "table": True, "noise_sigma": 40.0},
        ],
        ids=["centred", "off-centre", "bubble", "table", "all-at-once"],
    )
    def test_inner_radius_and_center(self, kwargs):
        expected_center = kwargs.get("center", (256.0, 256.0))
        geometry = detect_phantom(make_phantom(**kwargs))
        assert geometry.num_edge_points >= 60
        assert abs(geometry.radius - WATER_RADIUS) <= 0.5
        assert abs(geometry.center_row - expected_center[0]) <= 0.5
        assert abs(geometry.center_col - expected_center[1]) <= 0.5

    def test_not_the_outer_wall(self):
        geometry = detect_phantom(make_phantom())
        assert geometry.radius < WATER_RADIUS + WALL_PX / 2

    def test_initial_center_only_needs_to_be_in_the_water(self):
        geometry = detect_phantom(make_phantom(), initial_center=(300, 200))
        assert abs(geometry.radius - WATER_RADIUS) <= 0.5
        assert abs(geometry.center_row - 256.0) <= 0.5
        assert abs(geometry.center_col - 256.0) <= 0.5

    def test_no_phantom_falls_back_to_reconstruction_diameter(self):
        image = make_phantom()
        image.pixel_array[:] = AIR_HU
        geometry = detect_phantom(image)
        assert geometry.num_edge_points == 0
        assert geometry.diameter == pytest.approx(image.reconstruction_diameter / PIXEL_MM)

    def test_peripheral_rois_12_5_mm_from_inner_wall(self):
        image = make_phantom(center=(260.0, 250.0))
        rois = calculate_rois(image)
        cr, cc = 260.0, 250.0
        # Outer edge of each peripheral ROI, as a distance from the centre
        edges = [
            cr - (rois.top.center_row - rois.top.radius),
            (rois.right.center_col + rois.right.radius) - cc,
            (rois.bottom.center_row + rois.bottom.radius) - cr,
            cc - (rois.left.center_col - rois.left.radius),
        ]
        for edge in edges:
            gap_mm = (WATER_RADIUS - edge) * PIXEL_MM
            assert 12.5 - 1.0 <= gap_mm <= 12.5 + 1.0


# ---------------------------------------------------------------------------
# ANSM reference series (test_data/, gitignored)
# ---------------------------------------------------------------------------

def _inner_edges(profile: np.ndarray, start: int) -> tuple[int, int]:
    """Walk from `start` both ways while |HU| < 100: (first, last) water pixel."""
    left = right = start
    while right + 1 < len(profile) and abs(profile[right + 1]) < 100:
        right += 1
    while left - 1 >= 0 and abs(profile[left - 1]) < 100:
        left -= 1
    return left, right


@pytest.mark.skipif(not TEST_DATA_DIR.exists(), reason="ANSM reference series not available")
class TestAnsmSeries:
    @pytest.fixture(params=list(SERIES_INFO.keys()))
    def hu_image(self, request) -> DicomImage:
        name = request.param
        folder = TEST_DATA_DIR / name / SERIES_INFO[name]["dicom_subdir"]
        if not folder.exists():
            pytest.skip(f"{folder} not found")
        series = load_dicom_folder(folder)
        return series.images[series.num_images // 2]

    def test_radius_matches_profile_inner_wall(self, hu_image):
        geometry = detect_phantom(hu_image)
        assert geometry.num_edge_points >= 60
        cr, cc = geometry.center
        left, right = _inner_edges(hu_image.pixel_array[cr, :], cc)
        top, bottom = _inner_edges(hu_image.pixel_array[:, cc], cr)
        profile_radius = ((right - left) + (bottom - top)) / 4
        assert abs(geometry.radius - profile_radius) <= 1.5

    def test_peripheral_rois_in_the_regulatory_band(self, hu_image):
        rois = calculate_rois(hu_image)
        px = hu_image.pixel_size_mm
        cr, cc = rois.central.center_row, rois.central.center_col
        left, right = _inner_edges(hu_image.pixel_array[cr, :], cc)
        top, bottom = _inner_edges(hu_image.pixel_array[:, cc], cr)
        gaps_mm = [
            (rois.top.center_row - rois.top.radius - top) * px,
            (right - (rois.right.center_col + rois.right.radius)) * px,
            (bottom - (rois.bottom.center_row + rois.bottom.radius)) * px,
            (rois.left.center_col - rois.left.radius - left) * px,
        ]
        # Each ROI inside the regulatory band, and centred on it on average
        for gap in gaps_mm:
            assert 10.0 <= gap <= 15.0, gaps_mm
        assert abs(np.mean(gaps_mm) - 12.5) <= 1.0, gaps_mm
