"""One devices.json shared by several workstations: nothing a session wrote is lost."""

import json
import os
import time

import pytest

from cq_tdm.core import device_database
from cq_tdm.core.device_database import DeviceConfig, DeviceDatabase
from cq_tdm.core.qc_history import QCRun


def _run(uid: str, date: str = "2026-03-15", **kw) -> QCRun:
    return QCRun(run_date=date, series_uid=uid, recorded_at=f"{date}T10:00:00", **kw)


def _device(name: str = "CT1") -> DeviceConfig:
    return DeviceConfig.from_dicom("ACME", name, "ST", name)


@pytest.fixture
def shared(tmp_path):
    """A database file with one installation and one control, and two sessions opened on it."""
    path = tmp_path / "share" / "devices.json"
    path.parent.mkdir()
    seed = DeviceDatabase(path)
    device = _device()
    seed.save_device(device)
    seed.add_run(device.device_id, _run("R0", "2026-01-10"))
    return path, DeviceDatabase(path), DeviceDatabase(path), device.device_id


def _on_disk(path) -> dict:
    return {d["device_id"]: d for d in json.loads(path.read_text(encoding="utf-8"))["devices"]}


def _run_ids(device) -> list[str]:
    return [r["run_id"] if isinstance(r, dict) else r.run_id for r in
            (device["runs"] if isinstance(device, dict) else device.runs)]


# --- C-01: lost updates -------------------------------------------------------

def test_two_sessions_recording_a_control_keep_both(shared):
    path, a, b, dev = shared
    a.add_run(dev, _run("A1", "2026-04-01"))
    b.add_run(dev, _run("B1", "2026-07-01"))  # b has not seen A1

    assert _run_ids(_on_disk(path)[dev]) == ["R0", "A1", "B1"]
    assert _run_ids(b.get_device(dev)) == ["R0", "A1", "B1"]  # b took A1 in while saving
    assert b.take_foreign_changes() is True and b.take_foreign_changes() is False

    assert _run_ids(a.get_device(dev)) == ["R0", "A1"]
    assert a.refresh() is True
    assert _run_ids(a.get_device(dev)) == ["R0", "A1", "B1"]
    assert a.refresh() is False  # nothing new: one stat, no read


def test_stale_session_does_not_bring_back_a_deleted_installation(shared):
    path, a, b, dev = shared
    other = _device("CT2")
    a.save_device(other)
    b.refresh()
    a.delete_device(dev)
    b.add_run(other.device_id, _run("B1"))  # b still holds the deleted installation
    assert set(_on_disk(path)) == {other.device_id}
    assert b.get_device(dev) is None


def test_fields_of_one_installation_edited_on_two_workstations(shared):
    path, a, b, dev = shared
    a.get_device(dev).hospital_name = "CHU A"
    a.save_device(a.get_device(dev))
    b.get_device(dev).inventory_number = "INV-7"
    b.save_device(b.get_device(dev))
    saved = _on_disk(path)[dev]
    assert (saved["hospital_name"], saved["inventory_number"]) == ("CHU A", "INV-7")

    # The same field on both sides: the last writer wins, for that field only
    a.refresh()
    a.get_device(dev).hospital_location = "Salle 1"
    b.get_device(dev).hospital_location = "Salle 2"
    a.save_device(a.get_device(dev))
    b.save_device(b.get_device(dev))
    assert _on_disk(path)[dev]["hospital_location"] == "Salle 2"
    assert _on_disk(path)[dev]["hospital_name"] == "CHU A"


def test_deleted_control_stays_deleted_and_corrective_action_is_kept(shared):
    path, a, b, dev = shared
    a.add_run(dev, _run("A1", "2026-04-01"))
    b.refresh()

    a.delete_run(dev, "R0")
    run = a.get_device(dev).find_run("A1")
    run.corrective_action, run.corrective_action_date = "étalonnage refait", "2026-04-05"
    a.update_run(dev, run)

    b.add_run(dev, _run("B1", "2026-07-01"))  # b still holds R0 and the old A1
    saved = {r["run_id"]: r for r in _on_disk(path)[dev]["runs"]}
    assert list(saved) == ["A1", "B1"]
    assert saved["A1"]["corrective_action"] == "étalonnage refait"


def test_objects_held_by_the_interface_stay_valid(shared):
    _path, a, b, dev = shared
    device, run = a.get_device(dev), a.get_device(dev).runs[0]
    b.get_device(dev).hospital_name = "CHU B"
    b.save_device(b.get_device(dev))
    b.add_run(dev, _run("B1", "2026-07-01"))
    assert a.refresh() is True
    assert a.get_device(dev) is device and device.runs[0] is run  # same objects, new content
    assert device.hospital_name == "CHU B" and _run_ids(device) == ["R0", "B1"]


def test_refresh_keeps_changes_not_saved_yet(shared):
    path, a, b, dev = shared
    a.get_device(dev).reference_noise = 5.4  # typed, not saved
    b.add_run(dev, _run("B1", "2026-07-01"))
    assert a.refresh() is True
    assert a.get_device(dev).reference_noise == 5.4
    assert _run_ids(a.get_device(dev)) == ["R0", "B1"]
    a.save_device(a.get_device(dev))
    saved = _on_disk(path)[dev]
    assert saved["reference_noise"] == 5.4 and _run_ids(saved) == ["R0", "B1"]


def test_relocations_learnt_on_both_sides_are_kept(shared):
    path, a, b, _dev = shared
    a.add_relocation("/data", "/archive")
    b.add_relocation("D:/cq", "E:/cq")
    stored = json.loads(path.read_text(encoding="utf-8"))["folder_relocations"]
    assert stored == [{"old": "/data", "new": "/archive"}, {"old": "D:/cq", "new": "E:/cq"}]


def test_file_removed_meanwhile_is_recreated_from_memory(shared):
    path, a, _b, dev = shared
    path.unlink()
    a.add_run(dev, _run("A1", "2026-04-01"))
    assert _run_ids(_on_disk(path)[dev]) == ["R0", "A1"]  # not an empty database


def test_keys_written_by_another_version_survive_a_save(shared):
    path, a, _b, dev = shared
    data = json.loads(path.read_text(encoding="utf-8"))
    data["future_setting"] = {"x": 1}
    data["devices"][0]["future_field"] = "kept"
    path.write_text(json.dumps(data), encoding="utf-8")
    a.add_run(dev, _run("A1", "2026-04-01"))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["future_setting"] == {"x": 1}
    assert saved["devices"][0]["future_field"] == "kept"


# --- C-02: share not mounted --------------------------------------------------

def test_unreachable_folder_is_not_an_empty_database(tmp_path):
    path = tmp_path / "share" / "devices.json"  # the folder does not exist: share not mounted
    db = DeviceDatabase(path)
    assert db.read_only and "inaccessible" in db.read_only and db.load_error
    with pytest.raises(OSError, match="inaccessible"):
        db.save_device(_device("LOCAL"))
    assert not path.parent.exists()  # nothing was created in its place

    # The share comes back with the real database: it is read, never overwritten
    path.parent.mkdir()
    real = DeviceDatabase(path)
    real.save_device(_device("REAL"))
    assert db.refresh() is True
    assert db.read_only is None and db.load_error is None
    assert {d.dicom_model_name for d in db.get_all_devices()} == {"REAL", "LOCAL"}
    db.save_device(db.get_all_devices()[0])
    assert {d["dicom_model_name"] for d in _on_disk(path).values()} == {"REAL", "LOCAL"}


def test_missing_file_in_an_existing_folder_is_a_new_database(tmp_path):
    db = DeviceDatabase(tmp_path / "devices.json")
    assert db.read_only is None and db.load_error is None
    db.save_device(_device())
    assert (tmp_path / "devices.json").exists()


# --- C-28: file written by a newer version ------------------------------------

def test_file_of_a_newer_version_is_shown_but_not_written(shared):
    path, _a, _b, dev = shared
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = device_database.SCHEMA_VERSION + 1
    path.write_text(json.dumps(data), encoding="utf-8")
    content = path.read_text(encoding="utf-8")

    db = DeviceDatabase(path)
    assert db.get_device(dev) is not None  # readable
    assert db.read_only and "version plus récente" in db.read_only and db.load_error is None
    with pytest.raises(OSError, match="version plus récente"):
        db.add_run(dev, _run("X"))
    assert path.read_text(encoding="utf-8") == content  # untouched


def test_session_opened_before_the_upgrade_does_not_write_either(shared):
    path, a, _b, dev = shared
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = device_database.SCHEMA_VERSION + 1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(OSError, match="version plus récente"):
        a.add_run(dev, _run("A1"))
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == device_database.SCHEMA_VERSION + 1


# --- lock and backups ---------------------------------------------------------

def test_lock_held_by_another_workstation(shared, monkeypatch):
    path, a, _b, dev = shared
    lock = path.with_name("devices.json.lock")
    lock.write_text("other-host 1234", encoding="utf-8")
    monkeypatch.setattr(device_database, "LOCK_TIMEOUT_S", 0.3)
    with pytest.raises(OSError, match="en cours d'écriture sur un autre poste"):
        a.add_run(dev, _run("A1"))
    assert lock.exists()  # a live lock is never removed

    # A lock abandoned by a session that died is taken over
    old = time.time() - 10 * device_database.LOCK_STALE_S
    os.utime(lock, (old, old))
    a.add_run(dev, _run("A2", "2026-05-01"))
    assert "A2" in _run_ids(_on_disk(path)[dev])
    assert not lock.exists()  # released after the save


def test_no_lock_or_temporary_file_is_left_behind(shared):
    path, a, _b, dev = shared
    a.add_run(dev, _run("A1"))
    leftovers = [p.name for p in path.parent.iterdir()
                 if not p.name.endswith(".bak") and p.name != "devices.json"]
    assert leftovers == []


def test_one_dated_copy_per_day_and_old_ones_are_pruned(shared):
    path, a, _b, dev = shared
    for day in range(1, 15):
        (path.parent / f"devices.json.2025-01-{day:02d}.bak").write_text("{}", encoding="utf-8")
    a.add_run(dev, _run("A1"))
    a.add_run(dev, _run("A2", "2026-05-01"))
    copies = sorted(p.name for p in path.parent.glob("devices.json.????-??-??.bak"))
    assert len(copies) == device_database.DAILY_BACKUPS_KEPT
    assert copies[0] == "devices.json.2025-01-06.bak"  # the oldest ones went
    # Today's copy is the file as it was before the first write of the day, by
    # whichever session made it (here the one that created the installation)
    today = json.loads((path.parent / copies[-1]).read_text(encoding="utf-8"))
    assert today["devices"][0]["device_id"] == dev and today["devices"][0]["runs"] == []
