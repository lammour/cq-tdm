"""Core analysis modules for CQ TDM."""

from .dicom_loader import (
    DicomImage,
    DicomSeries,
    load_dicom_file,
    load_dicom_folder,
    detect_phantom_center,
    estimate_phantom_diameter,
)

from .water_phantom import (
    ROIDefinition,
    ROIMeasurement,
    WaterPhantomROIs,
    WaterPhantomResults,
    calculate_rois,
    measure_roi,
    analyze_water_phantom,
    format_results_text,
)

from .nps import (
    NPSROIPosition,
    NPSROIConfig,
    NPSResult,
    ROIUniformityWarning,
    calculate_nps_roi_positions,
    analyze_nps,
    format_nps_results_text,
)

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
