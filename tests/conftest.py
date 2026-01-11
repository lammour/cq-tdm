"""Pytest fixtures for CQ TDM tests."""

from pathlib import Path

import pytest


# Test data directory
TEST_DATA_DIR = Path(__file__).parent.parent / "test_data"

# Output directory for comparison plots
OUTPUT_DIR = Path(__file__).parent / "output"


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """Return the test data directory path."""
    assert TEST_DATA_DIR.exists(), f"Test data directory not found: {TEST_DATA_DIR}"
    return TEST_DATA_DIR


@pytest.fixture(scope="session")
def output_dir() -> Path:
    """Return the output directory for test artifacts, creating it if needed."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    return OUTPUT_DIR


# Series-specific fixtures
SERIES_INFO = {
    "Serie_1": {
        "dicom_subdir": "S1",
        "reference_freq": 0.263,  # Official ANSM reference value (mm^-1)
    },
    "Serie_2": {
        "dicom_subdir": "S2",
        "reference_freq": 0.266,
    },
    "Serie_3a": {
        "dicom_subdir": "S3a",
        "reference_freq": 0.247,
    },
    "Serie_3b": {
        "dicom_subdir": "S3b",
        "reference_freq": 0.237,
    },
    "Serie_4": {
        "dicom_subdir": "S4",
        "reference_freq": 0.338,
    },
}


@pytest.fixture(scope="session", params=list(SERIES_INFO.keys()))
def series_name(request) -> str:
    """Parameterized fixture providing each series name."""
    return request.param


@pytest.fixture(scope="session")
def series_info(series_name: str) -> dict:
    """Return info dict for the current series."""
    return SERIES_INFO[series_name]


@pytest.fixture(scope="session")
def series_dir(test_data_dir: Path, series_name: str) -> Path:
    """Return the directory for a specific series."""
    series_path = test_data_dir / series_name
    assert series_path.exists(), f"Series directory not found: {series_path}"
    return series_path


@pytest.fixture(scope="session")
def dicom_dir(series_dir: Path, series_info: dict) -> Path:
    """Return the DICOM subdirectory for a series."""
    dicom_path = series_dir / series_info["dicom_subdir"]
    assert dicom_path.exists(), f"DICOM directory not found: {dicom_path}"
    return dicom_path
