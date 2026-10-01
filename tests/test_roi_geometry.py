"""ROI geometry: sizing rules, freezing on the installation, persistence."""

import dataclasses

import numpy as np
import pytest

from cq_tdm.core.device_database import DeviceConfig, DeviceDatabase
from cq_tdm.core.dicom_loader import DicomImage
from cq_tdm.core.nps import analyze_nps, calculate_nps_roi_positions
from cq_tdm.core.qc_history import QCRun
from cq_tdm.core.roi_geometry import ROIGeometry
from cq_tdm.core.water_phantom import calculate_rois

from .test_phantom_detection import make_phantom, WATER_RADIUS, PIXEL_MM


def geometry_for(diameter_px: float, pixel_mm: float = 0.469, matrix: int = 512) -> ROIGeometry:
    return ROIGeometry.from_phantom(diameter_px, pixel_mm, matrix, matrix)


class TestSizingRules:
    @pytest.mark.parametrize("diameter_px", [404, 405, 434, 435, 438])
    def test_ansm_series_give_64_px(self, diameter_px):
        # Inner diameters of the five ANSM reference series (matrix 512)
        assert geometry_for(diameter_px).nps_roi_size == 64

    def test_1024_matrix_gives_128_px(self):
        assert geometry_for(870, pixel_mm=0.234, matrix=1024).nps_roi_size == 128

    def test_large_fov_is_floored_at_32_px(self):
        # 20 cm phantom in a 60 cm field of view: 174 px -> 26 -> floor
        assert geometry_for(174, pixel_mm=1.172).nps_roi_size == 32

    def test_capped_at_128_px(self):
        assert geometry_for(2000, pixel_mm=0.1, matrix=2048).nps_roi_size == 128

    def test_rounding_step_absorbs_diameter_jitter(self):
        sizes = {geometry_for(d).nps_roi_size for d in np.arange(430.0, 441.0, 0.5)}
        assert sizes == {64}

    def test_hu_rois_follow_the_decision(self):
        g = geometry_for(400, pixel_mm=0.5)
        assert g.central_radius == 80  # 40 % of the diameter
        assert g.peripheral_radius == 20  # 10 % of the diameter
        # Outer edge 12.5 mm = 25 px from the inner wall
        assert g.peripheral_distance == 200 - 25 - 20

    def test_peripheral_roi_never_below_100_pixels(self):
        g = geometry_for(100, pixel_mm=2.0)
        assert np.pi * g.peripheral_radius ** 2 >= 100

    def test_nps_rois_stay_inside_the_water_on_a_large_fov(self):
        # 16 cm phantom, 64 cm field of view: the old fixed 64 px ROIs spilled out
        for diameter_px in (128, 160, 174, 400, 870):
            g = geometry_for(diameter_px)
            far_corner = np.hypot(g.nps_diagonal_offset + g.nps_roi_size / 2,
                                  g.nps_diagonal_offset + g.nps_roi_size / 2)
            assert far_corner < diameter_px / 2, diameter_px
            assert g.nps_cardinal_distance + g.nps_roi_size / 2 < diameter_px / 2


class TestMatchesAndSerialization:
    def test_matches_same_format_only(self):
        image = make_phantom()
        g = geometry_for(2 * WATER_RADIUS, pixel_mm=PIXEL_MM)
        assert g.matches(image)
        assert not g.matches(dataclasses.replace(image, rows=1024, columns=1024))
        assert not g.matches(dataclasses.replace(image, pixel_spacing=(0.6, 0.6)))
        assert g.matches(dataclasses.replace(image, pixel_spacing=(PIXEL_MM * 1.001,) * 2))

    def test_round_trip_and_frozen_copy(self):
        g = geometry_for(400)
        assert not g.is_frozen
        f = g.frozen("2026-10-01")
        assert f.is_frozen and f.frozen_date == "2026-10-01"
        assert not g.is_frozen  # original untouched
        again = ROIGeometry.from_dict(f.to_dict())
        assert again == f
        assert ROIGeometry.from_dict(None) is None
        assert ROIGeometry.from_dict({"garbage": 1}) is None
        assert ROIGeometry.from_dict({**f.to_dict(), "unknown_key": 1}) == f

    def test_device_persists_geometry(self, tmp_path):
        db = DeviceDatabase(tmp_path / "devices.json")
        device = DeviceConfig.from_dicom("ACME", "CT1", "ST1", "SN1")
        device.roi_geometry = geometry_for(435).frozen("2026-10-01")
        device.runs.append(QCRun(run_date="2026-10-01", series_uid="A",
                                 roi_geometry=device.roi_geometry.to_dict()))
        db.save_device(device)
        reloaded = DeviceDatabase(tmp_path / "devices.json").get_device(device.device_id)
        assert reloaded.roi_geometry == device.roi_geometry
        assert ROIGeometry.from_dict(reloaded.runs[0].roi_geometry) == device.roi_geometry

    def test_device_without_geometry_loads(self, tmp_path):
        db = DeviceDatabase(tmp_path / "devices.json")
        device = DeviceConfig.from_dicom("ACME", "CT1", "ST1", "SN1")
        db.save_device(device)
        assert DeviceDatabase(tmp_path / "devices.json").get_device(device.device_id).roi_geometry is None


class TestFrozenGeometryIsReused:
    def test_hu_rois_keep_sizes_and_offsets_with_a_moved_phantom(self):
        reference = make_phantom(center=(256.0, 256.0))
        rois_ref = calculate_rois(reference)
        frozen = rois_ref.geometry.frozen("2026-10-01")

        # Next control: same phantom, placed 9 px lower and 6 px to the left,
        # and detected 1 px smaller (noise): frozen sizes and offsets must win
        later = make_phantom(center=(265.0, 250.0), seed=1)
        rois = calculate_rois(later, geometry=frozen)
        assert rois.geometry is frozen
        assert rois.central.radius == rois_ref.central.radius
        assert rois.top.radius == rois_ref.top.radius
        assert rois.central.center_row - rois.top.center_row == rois_ref.central.center_row - rois_ref.top.center_row
        assert abs(rois.central.center_row - 265) <= 1
        assert abs(rois.central.center_col - 250) <= 1

    def test_nps_positions_use_frozen_size(self):
        image = make_phantom()
        frozen = dataclasses.replace(geometry_for(2 * WATER_RADIUS, pixel_mm=PIXEL_MM),
                                     nps_roi_size=48, nps_cardinal_distance=70, nps_diagonal_offset=50)
        rois = calculate_nps_roi_positions(image, geometry=frozen)
        assert {r.side_square for r in rois} == {48}
        top = rois[4]
        assert abs((256 - top.y) - 70) <= 1

    def test_analyze_nps_reports_geometry_and_default_size(self):
        from cq_tdm.core.dicom_loader import DicomSeries
        series = DicomSeries(images=[make_phantom(seed=i) for i in range(3)])
        result = analyze_nps(series, slice_range=(0, 2))
        assert result.geometry is not None and not result.geometry.is_frozen
        # 400 px water disc -> 0.15 * 400 = 60, on the 56/64 rounding boundary
        assert result.roi_size == result.geometry.nps_roi_size
        assert result.roi_size in (56, 64)
        frozen = dataclasses.replace(result.geometry, nps_roi_size=32).frozen("2026-10-01")
        result2 = analyze_nps(series, slice_range=(0, 2), geometry=frozen)
        assert result2.roi_size == 32 and result2.geometry.is_frozen
