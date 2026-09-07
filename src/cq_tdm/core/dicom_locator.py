"""Find the DICOM folder of a recorded QC run after it has been moved.

A run remembers the folder it was analysed from (absolute path, plus the same
path relative to ``devices.json`` so an archive moved together with the
database still resolves). When neither resolves, known relocations of parent
directories are tried, and finally a directory can be scanned for the series.

Every candidate is verified against the run's SeriesInstanceUID before it is
accepted: the software never rebuilds a report from images that do not belong
to the recorded control.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

# Files that are never DICOM; skipped without opening
_SKIP_SUFFIXES = {".txt", ".pdf", ".xml", ".json", ".png", ".jpg", ".jpeg", ".csv", ".zip", ".html"}
# How many files of a folder are opened before deciding it holds no DICOM series
_MAX_PROBES_PER_FOLDER = 5

# (old_prefix, new_prefix) pairs learnt when the user relocates a folder
Relocation = tuple[str, str]


def read_series_uid(file_path: Path) -> Optional[str]:
    """SeriesInstanceUID of one file (header only), or None if it is not DICOM."""
    import pydicom

    try:
        ds = pydicom.dcmread(str(file_path), stop_before_pixels=True, specific_tags=["SeriesInstanceUID"])
    except Exception:
        return None
    uid = getattr(ds, "SeriesInstanceUID", None)
    return str(uid) if uid else None


def folder_series_uid(folder: Path) -> Optional[str]:
    """SeriesInstanceUID of the first DICOM file found directly in ``folder``."""
    try:
        entries = sorted(p for p in folder.iterdir() if p.is_file())
    except OSError:
        return None
    probes = 0
    for p in entries:
        if p.suffix.lower() in _SKIP_SUFFIXES:
            continue
        uid = read_series_uid(p)
        if uid:
            return uid
        probes += 1
        if probes >= _MAX_PROBES_PER_FOLDER:
            break
    return None


def folder_matches(folder: Path, series_uid: str) -> bool:
    """True when ``folder`` directly contains the series ``series_uid``."""
    if not series_uid or not folder.is_dir():
        return False
    return folder_series_uid(folder) == series_uid


def relative_to_database(folder: str | Path, db_path: Path) -> str:
    """``folder`` relative to the database directory, or "" (other drive, unrelated)."""
    try:
        return os.path.relpath(str(folder), str(db_path.parent))
    except ValueError:
        return ""


def candidate_folders(
    dicom_folder: str,
    dicom_folder_rel: str,
    db_path: Path,
    relocations: list[Relocation],
) -> list[Path]:
    """Folders to try, most likely first, without duplicates and without probing them."""
    out: list[Path] = []

    def add(p: str | Path | None):
        if not p:
            return
        p = Path(os.path.normpath(str(p)))  # "cfg/../data" -> "data"
        if p not in out:
            out.append(p)

    add(dicom_folder)
    if dicom_folder_rel:
        add(db_path.parent / dicom_folder_rel)
    if dicom_folder:
        for old_prefix, new_prefix in reversed(relocations):  # most recent first
            add(apply_relocation(dicom_folder, old_prefix, new_prefix))
    return out


def apply_relocation(path: str, old_prefix: str, new_prefix: str) -> Optional[str]:
    """``path`` with ``old_prefix`` swapped for ``new_prefix``; None if it does not apply."""
    old_parts = Path(old_prefix).parts
    parts = Path(path).parts
    if len(parts) < len(old_parts) or parts[:len(old_parts)] != old_parts:
        return None
    return str(Path(new_prefix).joinpath(*parts[len(old_parts):]))


def relocation_between(old_path: str, new_path: str) -> Optional[Relocation]:
    """The (old_prefix, new_prefix) pair that maps ``old_path`` onto ``new_path``.

    The common trailing part of the two paths is stripped, so moving
    ``/data/2026/S1`` to ``/archive/2026/S1`` learns ``/data`` → ``/archive``,
    which then resolves every other run stored under ``/data``. When nothing
    is common the pair maps just this folder.
    """
    if not old_path or not new_path:
        return None
    old_parts, new_parts = list(Path(old_path).parts), list(Path(new_path).parts)
    if old_parts == new_parts:
        return None
    common = 0
    while (common < len(old_parts) - 1 and common < len(new_parts) - 1
           and old_parts[-1 - common] == new_parts[-1 - common]):
        common += 1
    old_prefix = Path(*old_parts[:len(old_parts) - common]) if common else Path(old_path)
    new_prefix = Path(*new_parts[:len(new_parts) - common]) if common else Path(new_path)
    return str(old_prefix), str(new_prefix)


def resolve_dicom_folder(
    series_uid: str,
    dicom_folder: str,
    dicom_folder_rel: str,
    db_path: Path,
    relocations: list[Relocation],
) -> Optional[Path]:
    """First candidate folder that exists and holds ``series_uid``, or None."""
    for folder in candidate_folders(dicom_folder, dicom_folder_rel, db_path, relocations):
        if folder_matches(folder, series_uid):
            return folder
    return None


def find_series_folder(
    root: Path,
    series_uid: str,
    progress: Optional[Callable[[Path], bool]] = None,
) -> Optional[Path]:
    """Walk ``root`` for a folder holding ``series_uid``.

    Only a few file headers are read per folder, so a large archive scans
    quickly. ``progress`` is called with each folder visited and may return
    False to cancel the search.
    """
    if not series_uid:
        return None
    for dirpath, dirnames, _filenames in os.walk(root):
        dirnames.sort()
        folder = Path(dirpath)
        if progress is not None and progress(folder) is False:
            return None
        if folder_series_uid(folder) == series_uid:
            return folder
    return None
