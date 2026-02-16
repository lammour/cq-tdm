"""Core analysis modules for CQ TDM."""

# Lightweight imports - needed at startup
from .device_database import (
    DeviceConfig,
    DeviceDatabase,
)

from .app_config import (
    AppConfig,
    get_app_config,
    save_app_config,
)

from .utils import (
    format_fr,
    parse_float_fr,
)

# Lazy imports for heavy modules (pydicom, numpy, scipy)
_LAZY_IMPORTS = {
    # dicom_loader
    "DicomImage": ".dicom_loader",
    "DicomSeries": ".dicom_loader",
    "load_dicom_file": ".dicom_loader",
    "load_dicom_folder": ".dicom_loader",
    "detect_phantom_center": ".dicom_loader",
    "estimate_phantom_diameter": ".dicom_loader",
    # water_phantom
    "ROIDefinition": ".water_phantom",
    "ROIMeasurement": ".water_phantom",
    "WaterPhantomROIs": ".water_phantom",
    "WaterPhantomResults": ".water_phantom",
    "calculate_rois": ".water_phantom",
    "measure_roi": ".water_phantom",
    "analyze_water_phantom": ".water_phantom",
    "format_results_text": ".water_phantom",
    # nps
    "NPSROIPosition": ".nps",
    "NPSROIConfig": ".nps",
    "NPSResult": ".nps",
    "ROIUniformityWarning": ".nps",
    "calculate_nps_roi_positions": ".nps",
    "analyze_nps": ".nps",
    "format_nps_results_text": ".nps",
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        module_name = _LAZY_IMPORTS[name]
        import importlib
        module = importlib.import_module(module_name, __package__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
