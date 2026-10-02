"""The installations database: identities, missing installations, default location."""

import pytest

from cq_tdm.core.device_database import DeviceConfig, DeviceDatabase, DeviceNotFoundError
from cq_tdm.core.qc_history import QCRun


@pytest.fixture
def db(tmp_path):
    return DeviceDatabase(tmp_path / "devices.json")


def test_images_without_identity_do_not_share_an_installation(db):
    """Anonymised series carry no manufacturer, model, station or serial number."""
    first = DeviceConfig.from_dicom("", "", "", "")
    first.hospital_name = "CHU A"
    second = DeviceConfig.from_dicom("", "", "", "")
    second.hospital_name = "CHU B"
    db.save_device(first)
    db.save_device(second)

    assert first.device_id != second.device_id
    assert first.device_id.startswith("manual-")
    assert {d.hospital_name for d in DeviceDatabase(db.db_path).get_all_devices()} == {"CHU A", "CHU B"}
    # An empty identity names no scanner: nothing is "recognised" from it
    assert db.find_device("", "", "", "") is None


def test_identity_is_still_the_id_when_there_is_one(db):
    device = DeviceConfig.from_dicom("ACME", "CT 9000", "ST1", "SN42")
    db.save_device(device)
    assert device.device_id == "acme_ct-9000_st1_sn42"
    assert db.find_device("ACME", "CT 9000", "ST1", "SN42") is device


def test_recording_on_a_missing_installation_is_an_explicit_error(db):
    run = QCRun(run_date="2026-09-15", series_uid="1.2.3")
    with pytest.raises(DeviceNotFoundError, match="installation introuvable"):
        db.add_run("gone", run)
    with pytest.raises(LookupError):
        db.update_run("gone", run)


def test_default_database_sits_next_to_the_settings(tmp_path):
    # conftest redirects the configuration folder to tmp_path/config
    assert DeviceDatabase().db_path == tmp_path / "config" / "devices.json"
    assert DeviceDatabase.default_path() == tmp_path / "config" / "devices.json"
