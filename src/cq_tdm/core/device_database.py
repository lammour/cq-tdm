"""Device database for storing CT scanner configurations."""

import json
import os
import random
import shutil
import socket
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

from .app_config import AppConfig
from .qc_history import QCRun
from .utils import atomic_write_json
from .roi_geometry import ROIGeometry


class DeviceNotFoundError(LookupError):
    """The installation is no longer in the database (deleted, or another file was opened)."""

    def __init__(self, device_id: str):
        super().__init__(f"installation introuvable dans la base de données ({device_id or 'sans identifiant'})")


@dataclass
class DeviceConfig:
    """Configuration for a CT installation."""

    # Unique identifier (auto-generated from DICOM metadata)
    device_id: str = ""

    # DICOM identification fields (used for auto-detection)
    dicom_manufacturer: str = ""
    dicom_model_name: str = ""
    dicom_station_name: str = ""
    dicom_serial_number: str = ""

    # User-editable fields
    hospital_name: str = ""
    hospital_location: str = ""
    device_name: str = ""
    commissioning_date: str = ""
    serial_number: str = ""
    inventory_number: str = ""

    # Register of operations (ANSM decision, point 3.2.2): entered by hand,
    # printed on every report so that a report is a complete register entry
    phantom_brand: str = ""
    phantom_model: str = ""
    phantom_serial: str = ""
    clinical_protocol_origin: str = ""  # clinical protocol the QC protocol derives from
    reconstruction_algorithm: str = ""  # reconstruction algorithm and level of the QC protocol

    # Reference values for stability tests (ANSM)
    reference_noise: float | None = None  # Reference noise (σ) in HU
    reference_nps_freq: float | None = None  # Reference NPS mean frequency in cycles/mm

    # Saved slice selection values
    hu_slice_index: int | None = None  # HU analysis slice index
    nps_start_slice: int | None = None  # NPS analysis start slice
    nps_end_slice: int | None = None  # NPS analysis end slice

    # ROI sizes and offsets frozen from the reference control, so that every
    # later control uses identical ROIs (see core.roi_geometry)
    roi_geometry: ROIGeometry | None = None

    # Recorded QC controls, newest last (see qc_history.QCRun)
    runs: list[QCRun] = field(default_factory=list)
    # Entries of "runs" that could not be read (hand-edited file, other version):
    # not shown, but written back untouched so that saving never destroys them
    unreadable_runs: list = field(default_factory=list, compare=False)

    _NESTED = ("runs", "roi_geometry", "unreadable_runs")

    def to_dict(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in self._NESTED}
        d["roi_geometry"] = self.roi_geometry.to_dict() if self.roi_geometry else None
        d["runs"] = [r.to_dict() for r in self.runs] + list(self.unreadable_runs)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceConfig":
        known = {f.name for f in fields(cls)} - set(cls._NESTED)
        # Ignore unknown keys so a file written by a newer version still loads
        device = cls(**{k: v for k, v in data.items() if k in known})
        device.roi_geometry = ROIGeometry.from_dict(data.get("roi_geometry"))
        runs = data.get("runs") or []
        if not isinstance(runs, list):
            raise ValueError("liste des contrôles illisible")
        # One bad run must not hide the installation and its other controls
        for entry in runs:
            try:
                device.runs.append(QCRun.from_dict(entry))
            except (TypeError, ValueError):
                device.unreadable_runs.append(entry)
        return device

    def find_run(self, run_id: str) -> Optional[QCRun]:
        return next((r for r in self.runs if r.run_id == run_id), None)

    @classmethod
    def from_dicom(
        cls,
        manufacturer: str,
        model_name: str,
        station_name: str,
        serial_number: str = "",
    ) -> "DeviceConfig":
        """Create a new DeviceConfig from DICOM metadata."""
        device_id = cls.generate_id(manufacturer, model_name, station_name, serial_number)
        return cls(
            device_id=device_id,
            dicom_manufacturer=manufacturer or "",
            dicom_model_name=model_name or "",
            dicom_station_name=station_name or "",
            dicom_serial_number=serial_number or "",
            device_name=f"{manufacturer or ''} {model_name or ''}".strip(),
        )

    @staticmethod
    def generate_id(
        manufacturer: str,
        model_name: str,
        station_name: str,
        serial_number: str = "",
    ) -> str:
        """Generate a unique ID from DICOM metadata; "" when the identity is empty.

        Anonymised series can carry no manufacturer, model, station or serial
        number at all: such an image identifies no scanner, see ``new_manual_id``.
        """
        parts = [
            (manufacturer or "").strip().lower(),
            (model_name or "").strip().lower(),
            (station_name or "").strip().lower(),
            (serial_number or "").strip().lower(),
        ]
        return "_".join(p.replace(" ", "-") for p in parts if p)

    @staticmethod
    def new_manual_id() -> str:
        """Id of an installation created from images without any DICOM identity.

        Unique, so that two such installations never merge; a series without
        identity is then attached to its installation by hand.
        """
        return f"manual-{uuid.uuid4().hex[:8]}"

    def display_name(self) -> str:
        """Get a display name for the device.

        Format: Établissement - Équipement - N° inventaire
        Example: CHU Nantes - Siemens Naeotom Alpha - 2630499
        """
        parts = []

        # Hospital name
        if self.hospital_name:
            parts.append(self.hospital_name)

        # Device name (or fallback to DICOM info)
        if self.device_name:
            parts.append(self.device_name)
        elif self.dicom_manufacturer or self.dicom_model_name:
            device_parts = []
            if self.dicom_manufacturer:
                device_parts.append(self.dicom_manufacturer)
            if self.dicom_model_name:
                device_parts.append(self.dicom_model_name)
            parts.append(" ".join(device_parts))

        # Inventory number for disambiguation
        if self.inventory_number:
            parts.append(f"N°inv. {self.inventory_number}")

        if parts:
            return " - ".join(parts)

        # Fallback if nothing is filled
        return "Installation sans nom"


# Version of the file format written by this version of the software
SCHEMA_VERSION = 2

# Lock file: how long to wait for another workstation to finish writing, and
# the age after which a lock is considered abandoned (crash, power cut)
LOCK_TIMEOUT_S = 10.0
LOCK_STALE_S = 30.0
# Dated copies of the database kept next to it (one per day of use)
DAILY_BACKUPS_KEPT = 10

_MISSING = object()


def _three_way(base: dict, mine: dict, theirs: dict, merge_both) -> dict:
    """Merge two dicts that both derive from `base`, key by key.

    `theirs` is what is on disk now, `mine` what this session holds, `base`
    what this session last saw on disk. A key removed on one side stays
    removed unless the other side changed it meanwhile; a key present on both
    sides goes through `merge_both(base_value_or_None, mine_value, theirs_value)`.
    """
    merged = {}
    for key, their_value in theirs.items():
        if key in mine:
            merged[key] = merge_both(base.get(key), mine[key], their_value)
        elif key not in base:
            merged[key] = their_value  # added by another session
        # else: removed here
    for key, my_value in mine.items():
        if key in theirs:
            continue
        if key not in base or my_value != base[key]:
            merged[key] = my_value  # added here, or changed here while removed elsewhere
        # else: removed by another session, untouched here
    return merged


class DeviceDatabase:
    """Database for storing CT device configurations.

    The file can be shared between workstations. It is read once, then every
    save re-reads it under a short lock and merges what this session changed
    into what the others wrote meanwhile (see ``_save``), so that two sessions
    recording a control each never erase the other's.
    """

    def __init__(self, db_path: Optional[Path] = None, backup_unreadable: bool = True):
        """Initialize the database.

        Args:
            db_path: Path to the JSON database file. Defaults to user config directory.
            backup_unreadable: Copy an unreadable file aside (the default, for the
                database in use). False to merely probe a file the user points at.
        """
        self._backup_unreadable = backup_unreadable
        if db_path is None:
            db_path = self.default_path()

        self.db_path = db_path
        self._devices: dict[str, DeviceConfig] = {}
        # (old_prefix, new_prefix) pairs learnt when the user relocated a DICOM
        # folder; used to find the other folders of the same moved archive
        self.folder_relocations: list[tuple[str, str]] = []
        # Set when the existing file could not be read; the file is then backed up
        # before any save so that a corrupt database is never silently
        # overwritten with an empty one.
        self.load_error: str | None = None
        # Why nothing can be saved, when that is the case: the folder of the
        # database is unreachable (network share not mounted) or the file was
        # written by a newer version. None when the database can be written.
        self.read_only: str | None = None
        # One message per entry that was skipped while the rest of the file loaded
        self.load_warnings: list[str] = []
        # Entries of "devices" that could not be read, written back untouched
        self._unreadable_devices: list = []
        # What this session last saw on disk (see _snapshot), the common
        # ancestor of the merge done at every save
        self._base: dict[str, dict] = {}
        self._base_relocations: list[tuple[str, str]] = []
        # (mtime_ns, size) of the file when it was last read or written
        self._signature: tuple[int, int] | None = None
        self._last_backup_day: str = ""
        # True when a save brought in changes made on another workstation that
        # the interface has not been told about yet (see take_foreign_changes)
        self._foreign_changes: bool = False
        self._load()

    @staticmethod
    def default_path() -> Path:
        """Database used when none is configured: devices.json next to the settings.

        The folder is created when possible; on a read-only profile the
        application still opens and the first save reports the error.
        """
        return AppConfig.config_dir() / "devices.json"

    # ---- reading ----

    def _file_signature(self) -> tuple[int, int] | None:
        try:
            st = self.db_path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def _folder_reachable(self) -> bool:
        try:
            return self.db_path.parent.is_dir()
        except OSError:
            return False

    def _read_file(self) -> dict:
        """Parsed content of the file; OSError or ValueError when it cannot be used."""
        with open(self.db_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("devices", []), list):
            raise ValueError("ce n'est pas une base de données d'installations")
        return data

    @staticmethod
    def _newer_version(data: dict) -> str | None:
        """Why `data` must not be written by this version, or None."""
        version = data.get("version", 1)
        if isinstance(version, int) and version > SCHEMA_VERSION:
            return ("cette base de données a été écrite par une version plus récente de CQ TDM "
                    f"(format {version}) : mettez le logiciel à jour sur ce poste pour la modifier")
        return None

    def _load(self):
        """Load devices from the database file."""
        self.load_error = None
        self.read_only = None
        if not self.db_path.exists():
            # No file yet is normal (first use). No folder is not: a network
            # share that is not mounted must not look like an empty database,
            # which the next save would write over the real one.
            if not self._folder_reachable():
                self.read_only = (f"le dossier de la base de données est inaccessible : "
                                  f"{self.db_path.parent}")
                self.load_error = self.read_only
            return

        try:
            data = self._read_file()
            self._adopt(data)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            self.load_error = f"{self.db_path}: {e}"
            self._devices = {}
            self._unreadable_devices = []
            self.load_warnings = []
            self._base = {}
            if self._backup_unreadable:
                self._backup_unreadable_file()
            return
        self.read_only = self._newer_version(data)
        self._signature = self._file_signature()

    @staticmethod
    def _parse_devices(data: dict) -> tuple[dict[str, DeviceConfig], dict[str, dict], list, list[str]]:
        """Devices of a parsed file: (objects by id, raw dicts by id, unreadable entries, warnings)."""
        devices, raw_by_id, unreadable, warnings = {}, {}, [], []
        for i, raw in enumerate(data.get("devices", []), start=1):
            try:
                device = DeviceConfig.from_dict(raw)
            except (TypeError, ValueError, AttributeError) as e:
                unreadable.append(raw)
                warnings.append(f"installation n° {i} illisible ({e})")
                continue
            devices[device.device_id] = device
            raw_by_id[device.device_id] = raw
            if device.unreadable_runs:
                n = len(device.unreadable_runs)
                warnings.append(
                    f"{device.display_name()} : {n} contrôle{'s' if n > 1 else ''} "
                    f"illisible{'s' if n > 1 else ''}")
        return devices, raw_by_id, unreadable, warnings

    @staticmethod
    def _parse_relocations(data: dict) -> list[tuple[str, str]]:
        return [
            (str(r["old"]), str(r["new"]))
            for r in data.get("folder_relocations", [])
            if isinstance(r, dict) and r.get("old") and r.get("new")
        ]

    @staticmethod
    def _snapshot(devices: dict[str, DeviceConfig]) -> dict[str, dict]:
        """Comparable form of devices: fields and runs (by run id) as JSON values."""
        snapshot = {}
        for device_id, device in devices.items():
            fields_ = device.to_dict()
            fields_.pop("runs", None)
            runs = {run.run_id: run.to_dict() for run in device.runs}
            snapshot[device_id] = json.loads(json.dumps({"fields": fields_, "runs": runs}))
        return snapshot

    def _adopt(self, data: dict, base: dict | None = None):
        """Make the parsed file content `data` the in-memory state.

        Device and run objects that already exist are updated in place, so the
        references held by the interface stay valid. `base` is the state the
        next merge starts from; by default `data` itself (memory and disk agree).
        """
        fresh, _raw, unreadable, warnings = self._parse_devices(data)
        devices: dict[str, DeviceConfig] = {}
        for device_id, new in fresh.items():
            existing = self._devices.get(device_id)
            if existing is not None and existing is not new:
                self._update_in_place(existing, new)
                new = existing
            devices[device_id] = new
        self._devices = devices
        self._unreadable_devices = unreadable
        self.load_warnings = warnings
        self.folder_relocations = self._parse_relocations(data)
        if base is None:
            self._base = self._snapshot(devices)
            self._base_relocations = list(self.folder_relocations)
        else:
            base_devices, _, _, _ = self._parse_devices(base)
            self._base = self._snapshot(base_devices)
            self._base_relocations = self._parse_relocations(base)

    @staticmethod
    def _update_in_place(existing: DeviceConfig, new: DeviceConfig):
        """Give `existing` the content of `new`, keeping its run objects where they match."""
        runs_by_id = {run.run_id: run for run in existing.runs}
        runs = []
        for run in new.runs:
            kept = runs_by_id.get(run.run_id)
            if kept is not None:
                for f in fields(QCRun):
                    if f.name != "is_current":
                        setattr(kept, f.name, getattr(run, f.name))
                run = kept
            runs.append(run)
        for f in fields(DeviceConfig):
            if f.name != "runs":
                setattr(existing, f.name, getattr(new, f.name))
        existing.runs = runs

    def refresh(self) -> bool:
        """Take in what other workstations wrote since the last read; True if anything changed.

        Changes made in this session and not saved yet are kept. Costs one
        `stat` when the file has not changed.
        """
        signature = self._file_signature()
        if signature == self._signature:
            return False
        if signature is None:
            return False  # file gone (share unmounted): keep what is in memory
        try:
            theirs = self._read_file()
        except (OSError, ValueError):
            return False  # being written, or damaged: decided at the next save
        self._adopt(self._merge(theirs), base=theirs)
        self.load_error = None
        self.read_only = self._newer_version(theirs)
        self._signature = signature
        return True

    def take_foreign_changes(self) -> bool:
        """True, once, when a save merged in changes made on another workstation."""
        changed, self._foreign_changes = self._foreign_changes, False
        return changed

    # ---- writing ----

    def _backup_unreadable_file(self):
        """Copy an unreadable database file aside so it is not lost on the next save.

        The copy is dated: a second failure must not overwrite the first copy,
        which may be the only good one.
        """
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = self.db_path.with_name(f"{self.db_path.name}.illisible-{stamp}.bak")
        try:
            shutil.copy2(self.db_path, backup_path)
        except OSError:
            pass

    def _backup_daily(self):
        """Before the first write of the day, keep a dated copy of the file as it is.

        A regulatory record is cheap to copy and expensive to lose: the last
        DAILY_BACKUPS_KEPT copies stay next to the database.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_backup_day == today or not self.db_path.exists():
            return
        self._last_backup_day = today
        backup = self.db_path.with_name(f"{self.db_path.name}.{today}.bak")
        try:
            if not backup.exists():
                shutil.copy2(self.db_path, backup)
            dated = sorted(self.db_path.parent.glob(f"{self.db_path.name}.????-??-??.bak"))
            for old in dated[:-DAILY_BACKUPS_KEPT]:
                old.unlink()
        except OSError:
            pass  # a copy that cannot be made must not prevent the save

    def _server_now(self) -> float:
        """Current time as the folder's file system sees it (a workstation clock may differ)."""
        fd, name = tempfile.mkstemp(prefix=self.db_path.name + ".", suffix=".probe",
                                    dir=self.db_path.parent)
        try:
            return os.fstat(fd).st_mtime
        finally:
            os.close(fd)
            try:
                os.unlink(name)
            except OSError:
                pass

    @contextmanager
    def _lock(self):
        """Hold the lock file of the database while it is re-read, merged and written.

        The lock file is created atomically, which also works on network
        shares. A lock left by a session that died is taken over once it is
        LOCK_STALE_S old.
        """
        lock_path = self.db_path.with_name(self.db_path.name + ".lock")
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        next_stale_check = 0.0
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                now = time.monotonic()
                if now >= next_stale_check:
                    next_stale_check = now + 1.0
                    try:
                        stale = self._server_now() - lock_path.stat().st_mtime > LOCK_STALE_S
                    except OSError:
                        stale = False  # the lock was just released
                    if stale:
                        try:
                            lock_path.unlink()
                        except OSError:
                            pass
                        continue
                if now > deadline:
                    raise OSError(
                        "la base de données est en cours d'écriture sur un autre poste "
                        f"(verrou {lock_path.name}). Réessayez dans un instant.") from None
                # Uneven pauses, so that waiting sessions do not retry in step
                time.sleep(random.uniform(0.02, 0.1))
        try:
            os.write(fd, f"{socket.gethostname()} {os.getpid()} {datetime.now().isoformat()}".encode())
            os.close(fd)
            yield
        finally:
            try:
                lock_path.unlink()
            except OSError:
                pass

    def _merge(self, theirs: dict) -> dict:
        """File content that carries this session's changes on top of `theirs`.

        Installations, and within them fields and controls, are merged one by
        one against what this session last saw on disk: what was changed here
        wins, everything else is taken from the file. Two sessions editing the
        same field of the same installation is the only case where the last
        writer wins. Entries this version cannot read are kept as they are.
        """
        their_devices, their_raw, their_unreadable, _ = self._parse_devices(theirs)
        their_snapshot = self._snapshot(their_devices)
        mine = self._snapshot(self._devices)

        def merge_run(base_run, my_run, their_run):
            return my_run if base_run is None or my_run != base_run else their_run

        def merge_device(base_device, my_device, their_device):
            base_fields = base_device["fields"] if base_device else None
            merged_fields = dict(their_device["fields"])
            for key, value in my_device["fields"].items():
                if base_fields is None or value != base_fields.get(key, _MISSING):
                    merged_fields[key] = value
            return {
                "fields": merged_fields,
                "runs": _three_way(base_device["runs"] if base_device else {},
                                   my_device["runs"], their_device["runs"], merge_run),
            }

        merged = _three_way(self._base, mine, their_snapshot, merge_device)

        devices_out = []
        for device_id, device in merged.items():
            # Start from the raw entry of the file: keys written by a newer
            # version, and controls this version cannot read, are preserved
            raw = dict(their_raw.get(device_id, {}))
            raw.update(device["fields"])
            runs = sorted(device["runs"].values(),
                          key=lambda r: (r.get("run_date") or "", r.get("recorded_at") or ""))
            if device_id in their_devices:
                runs += list(their_devices[device_id].unreadable_runs)
            else:
                runs += list(self._devices[device_id].unreadable_runs)
            raw["runs"] = runs
            devices_out.append(raw)

        their_relocations = self._parse_relocations(theirs)
        relocations = [r for r in their_relocations
                       if r in self.folder_relocations or r not in self._base_relocations]
        relocations += [r for r in self.folder_relocations if r not in relocations]

        out = dict(theirs)
        out["version"] = SCHEMA_VERSION
        out["devices"] = devices_out + their_unreadable
        out["folder_relocations"] = [{"old": o, "new": n} for o, n in relocations]
        return out

    def _own_state(self) -> dict:
        """File content holding exactly what this session has in memory."""
        return {
            "version": SCHEMA_VERSION,
            "devices": [d.to_dict() for d in self._devices.values()] + self._unreadable_devices,
            "folder_relocations": [{"old": o, "new": n} for o, n in self.folder_relocations],
        }

    def _save(self):
        """Write the database: re-read the file, merge this session's changes into it, swap it in.

        Raises OSError when the database cannot be written (folder unreachable,
        file written by a newer version, lock held by another workstation).
        """
        if not self._folder_reachable():
            raise OSError(f"le dossier de la base de données est inaccessible : {self.db_path.parent}")
        with self._lock():
            if self.db_path.exists():
                try:
                    theirs = self._read_file()
                except ValueError:
                    # Damaged since it was read: keep a copy, then write what
                    # this session holds rather than merge with nothing
                    self._backup_unreadable_file()
                    theirs = None
            else:
                theirs = None  # first save, or file removed: recreate it from memory
            if theirs is not None:
                refused = self._newer_version(theirs)
                if refused:
                    self.read_only = refused
                    raise OSError(refused)
                self._backup_daily()
                data = self._merge(theirs)
            else:
                data = self._own_state()
            # Written to a temporary file then swapped in, so that a crash or a
            # full disk mid-write cannot leave a truncated database behind.
            atomic_write_json(self.db_path, data)
            before = self._snapshot(self._devices)
            self._adopt(data)
            if self._snapshot(self._devices) != before:
                self._foreign_changes = True
            self.load_error = None
            self.read_only = None
            self._signature = self._file_signature()

    def get_all_devices(self) -> list[DeviceConfig]:
        """Get all saved devices."""
        return list(self._devices.values())

    def get_device(self, device_id: str) -> Optional[DeviceConfig]:
        """Get a device by ID."""
        return self._devices.get(device_id)

    def _require(self, device_id: str) -> DeviceConfig:
        device = self._devices.get(device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        return device

    def find_device(
        self,
        manufacturer: str,
        model_name: str,
        station_name: str,
        serial_number: str = "",
    ) -> Optional[DeviceConfig]:
        """Find a device by DICOM metadata; None for an empty identity, which names no scanner."""
        device_id = DeviceConfig.generate_id(manufacturer, model_name, station_name, serial_number)
        if not device_id:
            return None
        return self._devices.get(device_id)

    def save_device(self, device: DeviceConfig):
        """Save or update a device in the database."""
        if not device.device_id:
            device.device_id = DeviceConfig.generate_id(
                device.dicom_manufacturer,
                device.dicom_model_name,
                device.dicom_station_name,
                device.dicom_serial_number,
            ) or DeviceConfig.new_manual_id()
        self._devices[device.device_id] = device
        self._save()

    def add_run(self, device_id: str, run: QCRun) -> bool:
        """Record a QC run for a device; a run with the same id (same series) is replaced.

        The replaced run hands its corrective action over to the new one.
        Returns True when an existing run was replaced.
        """
        device = self._require(device_id)
        replaced = False
        for i, existing in enumerate(device.runs):
            if existing.run_id == run.run_id:
                # The corrective action was entered on the recorded control, after
                # the fact: a new export of the series must not erase it
                if not (run.corrective_action_date or run.corrective_action):
                    run.corrective_action_date = existing.corrective_action_date
                    run.corrective_action = existing.corrective_action
                device.runs[i] = run
                replaced = True
                break
        else:
            device.runs.append(run)
        device.runs.sort(key=lambda r: (r.run_date, r.recorded_at))
        self._save()
        return replaced

    def update_run(self, device_id: str, run: QCRun):
        """Persist changes made to a run object that already belongs to the device."""
        device = self._require(device_id)
        if not any(r is run for r in device.runs):
            raise ValueError("ce contrôle n'appartient pas à cette installation")
        self._save()

    def delete_run(self, device_id: str, run_id: str) -> bool:
        device = self._devices.get(device_id)
        if device is None:
            return False
        before = len(device.runs)
        device.runs = [r for r in device.runs if r.run_id != run_id]
        if len(device.runs) != before:
            self._save()
            return True
        return False

    def add_relocation(self, old_prefix: str, new_prefix: str, keep: int = 20):
        """Remember that folders under ``old_prefix`` now live under ``new_prefix``."""
        pair = (old_prefix, new_prefix)
        self.folder_relocations = [r for r in self.folder_relocations if r != pair]
        self.folder_relocations.append(pair)
        del self.folder_relocations[:-keep]
        self._save()

    def delete_device(self, device_id: str) -> bool:
        """Delete a device from the database."""
        if device_id in self._devices:
            del self._devices[device_id]
            self._save()
            return True
        return False

    def device_exists(self, device_id: str) -> bool:
        """Check if a device exists in the database."""
        return device_id in self._devices
