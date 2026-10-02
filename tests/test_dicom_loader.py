"""Loading a folder: only the axial CT slices of one series are analysed."""

import numpy as np
import pytest

from cq_tdm.core.dicom_loader import NotACTSlice, load_dicom_file, load_dicom_folder

from .dicom_factory import LOCALIZER, SECONDARY_CAPTURE, write_ct_slice, write_series

UID_A = "1.2.826.0.1.3680043.8.498.10"
UID_B = "1.2.826.0.1.3680043.8.498.20"


def test_pixels_are_converted_to_hu(tmp_path):
    pixels = np.arange(16, dtype=float).reshape(4, 4) - 8
    path = write_ct_slice(tmp_path, "IM1", pixels=pixels)
    image = load_dicom_file(path)
    assert np.array_equal(image.pixel_array, pixels)
    assert image.pixel_size_mm == 0.5
    assert image.image_type == "ORIGINAL/PRIMARY/AXIAL"


def test_slices_are_ordered_by_position(tmp_path):
    for name, z in (("b", 10.0), ("a", 5.0), ("c", 0.0)):
        write_ct_slice(tmp_path, name, z=z)
    series = load_dicom_folder(tmp_path)
    assert [img.slice_location for img in series.images] == [0.0, 5.0, 10.0]
    assert not series.load_errors and not series.skipped


# --- pixel spacing (every mm and every frequency derives from it) -----------

@pytest.mark.parametrize("spacing, reason", [
    (None, "taille de pixel absente"),
    ((0, 0), "taille de pixel invalide"),
    ((0.5, 0.7), "pixels non carrés"),
])
def test_series_without_usable_pixel_spacing_is_refused(tmp_path, spacing, reason):
    write_series(tmp_path, 3, pixel_spacing=spacing)
    series = load_dicom_folder(tmp_path)
    assert series.is_empty
    assert len(series.load_errors) == 3
    assert all(reason in e for e in series.load_errors)


def test_one_slice_without_pixel_spacing_is_reported(tmp_path):
    write_series(tmp_path, 3)
    write_ct_slice(tmp_path, "IM9999", z=99.0, pixel_spacing=None)
    series = load_dicom_folder(tmp_path)
    assert series.num_images == 3
    assert len(series.load_errors) == 1 and series.load_errors[0].startswith("IM9999 : ")


# --- objects that are not axial CT slices -----------------------------------

def test_topogram_and_dose_report_are_left_out(tmp_path):
    write_series(tmp_path, 4)
    write_ct_slice(tmp_path, "TOPO", image_type=LOCALIZER, series_uid=UID_B, series_number=1,
                   orientation=(1, 0, 0, 0, 0, -1))
    write_ct_slice(tmp_path, "DOSE", sop_class=SECONDARY_CAPTURE, series_uid="1.2.3.99", series_number=999)
    write_ct_slice(tmp_path, "MR", modality="MR", series_uid="1.2.3.98")
    series = load_dicom_folder(tmp_path)

    assert series.num_images == 4
    assert {img.series_instance_uid for img in series.images} == {UID_A}
    assert not series.load_errors  # leaving them out is expected, not an error
    reasons = " | ".join(series.skipped)
    assert "TOPO : topogramme" in reasons
    assert "DOSE : objet DICOM qui n'est pas une coupe TDM" in reasons
    assert "MR : modalité MR" in reasons
    assert [s.series_uid for s in series.available_series] == [UID_A]


def test_localizer_raises_not_a_ct_slice(tmp_path):
    path = write_ct_slice(tmp_path, "TOPO", image_type=LOCALIZER)
    with pytest.raises(NotACTSlice, match="topogramme"):
        load_dicom_file(path)


def test_coronal_reformat_is_left_out(tmp_path):
    write_series(tmp_path, 2)
    write_ct_slice(tmp_path, "COR", orientation=(1, 0, 0, 0, 0, -1), series_uid=UID_B)
    series = load_dicom_folder(tmp_path)
    assert series.num_images == 2
    assert any("coupe non axiale" in s for s in series.skipped)


def test_helical_and_sequential_slices_are_both_accepted(tmp_path):
    """The acquisition mode is not a criterion: both give axial slices."""
    write_ct_slice(tmp_path, "H", z=0, ScanOptions="HELICAL MODE")
    write_ct_slice(tmp_path, "S", z=5, ScanOptions="AXIAL MODE")
    write_ct_slice(tmp_path, "N", z=10, image_type=None)  # no ImageType at all
    assert load_dicom_folder(tmp_path).num_images == 3


def test_lossy_compressed_image_is_refused(tmp_path):
    write_ct_slice(tmp_path, "IM1", LossyImageCompression="01")
    series = load_dicom_folder(tmp_path)
    assert series.is_empty and "compressée avec perte" in series.load_errors[0]


# --- several series in one folder -------------------------------------------

def test_series_are_never_mixed(tmp_path):
    write_series(tmp_path, 5, prefix="A", series_uid=UID_A, series_number=2, description="Filtre mou")
    write_series(tmp_path, 3, prefix="B", series_uid=UID_B, series_number=3, description="Filtre dur")

    series = load_dicom_folder(tmp_path)
    assert series.series_uid == UID_A and series.num_images == 5  # the most populated one
    assert [(s.series_uid, s.num_images) for s in series.available_series] == [(UID_A, 5), (UID_B, 3)]
    assert [s.series_uid for s in series.other_series] == [UID_B]
    assert series.available_series[1].label() == "Série 3 — Filtre dur — 3 coupes"

    chosen = load_dicom_folder(tmp_path, series_uid=UID_B)
    assert chosen.series_uid == UID_B and chosen.num_images == 3
    assert {img.series_instance_uid for img in chosen.images} == {UID_B}


def test_slice_with_another_matrix_is_refused(tmp_path):
    write_series(tmp_path, 3)
    write_ct_slice(tmp_path, "ODD", z=50.0, rows=16, columns=16)
    series = load_dicom_folder(tmp_path)
    assert series.num_images == 3
    assert series.load_errors == ["ODD : matrice ou taille de pixel différente du reste de la série"]


# --- files that are not DICOM images -----------------------------------------

def test_hidden_files_and_dicomdir_are_not_opened(tmp_path):
    write_series(tmp_path, 2)
    for name in (".DS_Store", "DICOMDIR", "Thumbs.db", "notes.txt", "desktop.ini"):
        (tmp_path / name).write_bytes(b"not dicom")
    series = load_dicom_folder(tmp_path)
    assert series.num_images == 2
    assert not series.load_errors and not series.skipped


def test_unreadable_file_is_reported(tmp_path):
    write_series(tmp_path, 2)
    (tmp_path / "IM9999").write_bytes(b"\x00" * 300)
    series = load_dicom_folder(tmp_path)
    assert series.num_images == 2
    assert len(series.load_errors) == 1 and series.load_errors[0].startswith("IM9999 : ")


def test_path_must_be_an_existing_folder(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_dicom_folder(tmp_path / "missing")
    path = write_ct_slice(tmp_path, "IM1")
    with pytest.raises(NotADirectoryError):
        load_dicom_folder(path)


def test_empty_folder_gives_an_empty_series(tmp_path):
    series = load_dicom_folder(tmp_path)
    assert series.is_empty and series.series_uid == "" and series.available_series == []
