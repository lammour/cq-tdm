"""Device database for storing CT scanner configurations."""

import json
import shutil
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

from .app_config import AppConfig
from .qc_history import QCRun
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


class DeviceDatabase:
    """Database for storing CT device configurations."""

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize the database.

        Args:
            db_path: Path to the JSON database file. Defaults to user config directory.
        """
        if db_path is None:
            db_path = self.default_path()

        self.db_path = db_path
        self._devices: dict[str, DeviceConfig] = {}
        # (old_prefix, new_prefix) pairs learnt when the user relocated a DICOM
        # folder; used to find the other folders of the same moved archive
        self.folder_relocations: list[tuple[str, str]] = []
        # Set when the existing file could not be read; the file is then backed up
        # before any save so that a corrupt or newer-format database is never
        # silently overwritten with an empty one.
        self.load_error: str | None = None
        # One message per entry that was skipped while the rest of the file loaded
        self.load_warnings: list[str] = []
        # Entries of "devices" that could not be read, written back untouched
        self._unreadable_devices: list = []
        self._load()

    @staticmethod
    def default_path() -> Path:
        """Database used when none is configured: devices.json next to the settings.

        The folder is created when possible; on a read-only profile the
        application still opens and the first save reports the error.
        """
        return AppConfig.config_dir() / "devices.json"

    def _load(self):
        """Load devices from the database file."""
        if not self.db_path.exists():
            return

        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for i, device_data in enumerate(data.get("devices", []), start=1):
                try:
                    device = DeviceConfig.from_dict(device_data)
                except (TypeError, ValueError, AttributeError) as e:
                    self._unreadable_devices.append(device_data)
                    self.load_warnings.append(f"installation n° {i} illisible ({e})")
                    continue
                self._devices[device.device_id] = device
                if device.unreadable_runs:
                    n = len(device.unreadable_runs)
                    self.load_warnings.append(
                        f"{device.display_name()} : {n} contrôle{'s' if n > 1 else ''} "
                        f"illisible{'s' if n > 1 else ''}")
            self.folder_relocations = [
                (str(r["old"]), str(r["new"]))
                for r in data.get("folder_relocations", [])
                if isinstance(r, dict) and r.get("old") and r.get("new")
            ]
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            self.load_error = f"{self.db_path}: {e}"
            self._devices = {}
            self._unreadable_devices = []
            self.load_warnings = []
            self._backup_unreadable_file()

    def _backup_unreadable_file(self):
        """Copy an unreadable database file aside so it is not lost on the next save."""
        backup_path = self.db_path.with_suffix(self.db_path.suffix + ".bak")
        try:
            shutil.copy2(self.db_path, backup_path)
        except OSError:
            pass

    def _save(self):
        """Save devices to the database file."""
        data = {
            "version": 2,
            "devices": [d.to_dict() for d in self._devices.values()] + self._unreadable_devices,
            "folder_relocations": [{"old": o, "new": n} for o, n in self.folder_relocations],
        }
        # Write to a temporary file and swap it in, so that a crash or a full
        # disk mid-write cannot leave a truncated database behind.
        tmp_path = self.db_path.with_name(self.db_path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp_path.replace(self.db_path)

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
