"""Device database for storing CT scanner configurations."""

import json
import shutil
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QStandardPaths

from .qc_history import QCRun


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

    # Reference values for stability tests (ANSM)
    reference_noise: float | None = None  # Reference noise (σ) in HU
    reference_nps_freq: float | None = None  # Reference NPS mean frequency in cycles/mm

    # Saved slice selection values
    hu_slice_index: int | None = None  # HU analysis slice index
    nps_start_slice: int | None = None  # NPS analysis start slice
    nps_end_slice: int | None = None  # NPS analysis end slice

    # Recorded QC controls, newest last (see qc_history.QCRun)
    runs: list[QCRun] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "runs"}
        d["runs"] = [r.to_dict() for r in self.runs]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceConfig":
        known = {f.name for f in fields(cls)} - {"runs"}
        # Ignore unknown keys so a file written by a newer version still loads
        device = cls(**{k: v for k, v in data.items() if k in known})
        device.runs = [QCRun.from_dict(r) for r in data.get("runs", []) if isinstance(r, dict)]
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
            device_name=f"{manufacturer} {model_name}".strip() or "[Nom de l'équipement]",
        )

    @staticmethod
    def generate_id(
        manufacturer: str,
        model_name: str,
        station_name: str,
        serial_number: str = "",
    ) -> str:
        """Generate a unique ID from DICOM metadata."""
        parts = [
            (manufacturer or "").strip().lower(),
            (model_name or "").strip().lower(),
            (station_name or "").strip().lower(),
            (serial_number or "").strip().lower(),
        ]
        return "_".join(p.replace(" ", "-") for p in parts if p)

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
        return "Équipement inconnu"


class DeviceDatabase:
    """Database for storing CT device configurations."""

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize the database.

        Args:
            db_path: Path to the JSON database file. Defaults to user config directory.
        """
        if db_path is None:
            # Use platform-specific config directory
            base_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)
            config_dir = Path(base_path) / "cq_tdm"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = config_dir / "devices.json"

        self.db_path = db_path
        self._devices: dict[str, DeviceConfig] = {}
        # Set when the existing file could not be read; the file is then backed up
        # before any save so that a corrupt or newer-format database is never
        # silently overwritten with an empty one.
        self.load_error: str | None = None
        self._load()

    def _load(self):
        """Load devices from the database file."""
        if not self.db_path.exists():
            return

        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for device_data in data.get("devices", []):
                device = DeviceConfig.from_dict(device_data)
                self._devices[device.device_id] = device
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            self.load_error = f"{self.db_path}: {e}"
            self._devices = {}
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
            "devices": [d.to_dict() for d in self._devices.values()]
        }
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_all_devices(self) -> list[DeviceConfig]:
        """Get all saved devices."""
        return list(self._devices.values())

    def get_device(self, device_id: str) -> Optional[DeviceConfig]:
        """Get a device by ID."""
        return self._devices.get(device_id)

    def find_device(
        self,
        manufacturer: str,
        model_name: str,
        station_name: str,
        serial_number: str = "",
    ) -> Optional[DeviceConfig]:
        """Find a device by DICOM metadata."""
        device_id = DeviceConfig.generate_id(manufacturer, model_name, station_name, serial_number)
        return self._devices.get(device_id)

    def save_device(self, device: DeviceConfig):
        """Save or update a device in the database."""
        if not device.device_id:
            device.device_id = DeviceConfig.generate_id(
                device.dicom_manufacturer,
                device.dicom_model_name,
                device.dicom_station_name,
                device.dicom_serial_number,
            )
        self._devices[device.device_id] = device
        self._save()

    def add_run(self, device_id: str, run: QCRun) -> bool:
        """Record a QC run for a device; a run with the same id (same series) is replaced.

        Returns True when an existing run was replaced.
        """
        device = self._devices[device_id]
        replaced = False
        for i, existing in enumerate(device.runs):
            if existing.run_id == run.run_id:
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
        device = self._devices[device_id]
        if not any(r is run for r in device.runs):
            raise ValueError("run does not belong to this device")
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
