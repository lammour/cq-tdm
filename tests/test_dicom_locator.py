"""Tests for finding a run's DICOM folder after it has been moved."""

from pathlib import Path

import pytest

from cq_tdm.core.device_database import DeviceDatabase
from cq_tdm.core.dicom_locator import (
    apply_relocation,
    candidate_folders,
    find_series_folder,
    folder_matches,
    folder_series_uid,
    relative_to_database,
    relocation_between,
    resolve_dicom_folder,
)

UID_A = "1.2.826.0.1.3680043.8.498.1"
UID_B = "1.2.826.0.1.3680043.8.498.2"


def _write_dicom(folder: Path, name: str, series_uid: str):
    """Write a header-only DICOM file (no pixel data) with the given series UID."""
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

    folder.mkdir(parents=True, exist_ok=True)
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.SeriesInstanceUID = series_uid
    ds.Modality = "CT"
    try:
        ds.save_as(str(folder / name), enforce_file_format=True)  # pydicom 3
    except TypeError:
        ds.is_little_endian, ds.is_implicit_VR = True, False
        ds.save_as(str(folder / name), write_like_original=False)  # pydicom 2


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    _write_dicom(root / "2026" / "S1", "IM0001", UID_A)
    _write_dicom(root / "2026" / "S1", "IM0002", UID_A)
    (root / "2026" / "S1" / "notes.txt").write_text("not dicom", encoding="utf-8")
    _write_dicom(root / "2026" / "S2", "IM0001", UID_B)
    (root / "2026" / "empty").mkdir()
    (root / "2026" / "junk").mkdir()
    (root / "2026" / "junk" / "a.bin").write_bytes(b"\x00" * 300)
    return root


def test_folder_uid_and_match(archive: Path):
    assert folder_series_uid(archive / "2026" / "S1") == UID_A
    assert folder_series_uid(archive / "2026" / "junk") is None
    assert folder_series_uid(archive / "2026" / "empty") is None
    assert folder_matches(archive / "2026" / "S1", UID_A)
    assert not folder_matches(archive / "2026" / "S1", UID_B)
    assert not folder_matches(archive / "missing", UID_A)
    assert not folder_matches(archive / "2026" / "S1", "")


def test_relocation_between_strips_common_suffix():
    assert relocation_between("/data/2026/S1", "/archive/2026/S1") == ("/data", "/archive")
    assert relocation_between("/data/2026/S1", "/archive/old/2026/S1") == ("/data", "/archive/old")
    # Nothing in common: maps this one folder only
    assert relocation_between("/data/2026/S1", "/mnt/x/Y") == ("/data/2026/S1", "/mnt/x/Y")
    assert relocation_between("/data/2026/S1", "/data/2026/S1") is None
    assert relocation_between("", "/x") is None


def test_apply_relocation():
    assert apply_relocation("/data/2026/S2", "/data", "/archive") == str(Path("/archive/2026/S2"))
    assert apply_relocation("/other/2026/S2", "/data", "/archive") is None
    assert apply_relocation("/dat", "/data", "/archive") is None


def test_candidates_order(tmp_path: Path):
    db = tmp_path / "cfg" / "devices.json"
    cands = candidate_folders("/data/2026/S1", "../data/2026/S1", db, [("/old", "/new"), ("/data", "/archive")])
    assert cands[0] == Path("/data/2026/S1")
    assert cands[1] == tmp_path / "data" / "2026" / "S1"  # normalised
    assert cands[2] == Path("/archive/2026/S1")  # most recent relocation first
    assert len(cands) == 3  # the /old rule does not apply


def test_resolve_after_move_via_relocation(archive: Path, tmp_path: Path):
    db = tmp_path / "cfg" / "devices.json"
    old_s1 = str(archive / "2026" / "S1")
    old_s2 = str(archive / "2026" / "S2")
    moved = tmp_path / "archive"
    (archive).rename(moved)

    assert resolve_dicom_folder(UID_A, old_s1, "", db, []) is None
    reloc = relocation_between(old_s1, str(moved / "2026" / "S1"))
    assert reloc == (str(archive), str(moved))
    # One learnt relocation resolves the other run of the same archive
    assert resolve_dicom_folder(UID_B, old_s2, "", db, [reloc]) == moved / "2026" / "S2"
    # A wrong UID is never accepted even when the path exists
    assert resolve_dicom_folder(UID_A, old_s2, "", db, [reloc]) is None


def test_resolve_via_path_relative_to_database(archive: Path, tmp_path: Path):
    db = tmp_path / "cfg" / "devices.json"
    rel = relative_to_database(archive / "2026" / "S1", db)
    assert rel == str(Path("..") / "data" / "2026" / "S1")
    # Database and archive moved together: absolute path is stale, relative one resolves
    new_root = tmp_path / "usb"
    (new_root / "cfg").mkdir(parents=True)
    (tmp_path / "data").rename(new_root / "data")
    new_db = new_root / "cfg" / "devices.json"
    assert resolve_dicom_folder(UID_A, str(archive / "2026" / "S1"), rel, new_db, []) == \
        new_root / "data" / "2026" / "S1"


def test_find_series_folder_scans_and_cancels(archive: Path):
    assert find_series_folder(archive, UID_B) == archive / "2026" / "S2"
    assert find_series_folder(archive, "9.9.9") is None
    visited = []

    def stop_at_second(folder: Path) -> bool:
        visited.append(folder)
        return len(visited) < 2

    assert find_series_folder(archive, UID_B, progress=stop_at_second) is None
    assert len(visited) == 2


def test_database_persists_relocations(tmp_path: Path):
    db_path = tmp_path / "devices.json"
    db = DeviceDatabase(db_path)
    db.add_relocation("/data", "/archive")
    db.add_relocation("/data", "/archive")  # no duplicate
    db.add_relocation("/x", "/y")
    assert DeviceDatabase(db_path).folder_relocations == [("/data", "/archive"), ("/x", "/y")]
