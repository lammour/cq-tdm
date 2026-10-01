"""Confirm device_database behaviours: all-or-nothing load, lost update, missing file."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "/home/user/cq-tdm/src")
from cq_tdm.core.device_database import DeviceConfig, DeviceDatabase
from cq_tdm.core.qc_history import QCRun

tmp = Path(tempfile.mkdtemp())
db_path = tmp / "devices.json"

# 1. One run missing 'run_date' (e.g. written by a future version / hand-edited) -> whole DB unreadable?
db = DeviceDatabase(db_path)
good = DeviceConfig.from_dicom("ACME", "CT1", "ST1", "SN1")
db.save_device(good)
db.add_run(good.device_id, QCRun(run_date="2026-01-01", series_uid="A"))
other = DeviceConfig.from_dicom("ACME", "CT2", "ST2", "SN2")
db.save_device(other)
data = json.loads(db_path.read_text())
del data["devices"][1]["runs"]  # keep
data["devices"][0]["runs"].append({"series_uid": "B", "noise": 3.0})  # no run_date
db_path.write_text(json.dumps(data))
reloaded = DeviceDatabase(db_path)
print("1) load_error:", reloaded.load_error)
print("   devices visible after one bad run entry:", len(reloaded.get_all_devices()), "(expected 2 if tolerant)")
print("   .bak exists:", (tmp / "devices.json.bak").exists())

# 2. Lost update between two instances (two workstations sharing devices.json)
db_path2 = tmp / "shared.json"
a = DeviceDatabase(db_path2); a.save_device(DeviceConfig.from_dicom("ACME", "CT1", "ST1", "SN1"))
b = DeviceDatabase(db_path2)          # workstation B loads
a.add_run("acme_ct1_st1_sn1", QCRun(run_date="2026-02-01", series_uid="A1"))  # A records a control
b.add_run("acme_ct1_st1_sn1", QCRun(run_date="2026-03-01", series_uid="B1"))  # B records a control
final = DeviceDatabase(db_path2).get_device("acme_ct1_st1_sn1").runs
print("2) runs on disk after A then B saved:", [r.series_uid for r in final], "(expected ['A1','B1'])")

# 3. Share temporarily unreachable: file 'missing' -> empty DB, no load_error, next save overwrites
db_path3 = tmp / "net" / "devices.json"
db_path3.parent.mkdir()
c = DeviceDatabase(db_path3); c.save_device(DeviceConfig.from_dicom("ACME", "CT1", "ST1", "SN1"))
(tmp / "net").rename(tmp / "net_offline")      # simulate share unreachable at startup
d = DeviceDatabase(db_path3)
print("3) load_error when file unreachable:", d.load_error, "| devices:", len(d.get_all_devices()))
(tmp / "net_offline").rename(tmp / "net")      # share back online
d.save_device(DeviceConfig.from_dicom("OTHER", "X", "Y", "Z"))
print("   devices on disk after the 'offline' instance saved:", [x.device_id for x in DeviceDatabase(db_path3).get_all_devices()])

# 4. add_run on unknown device
try:
    c.add_run("nope", QCRun(run_date="2026-01-01"))
except Exception as e:
    print("4) add_run unknown device ->", type(e).__name__, e)

# 5. default-path constructor when config dir is not writable (mkdir raises)
import cq_tdm.core.device_database as dd
class FakeQSP:
    class StandardLocation:
        GenericConfigLocation = 0
    @staticmethod
    def writableLocation(_):
        return "/proc/forbidden"
dd.QStandardPaths = FakeQSP
try:
    DeviceDatabase()
except Exception as e:
    print("5) DeviceDatabase() unwritable config dir ->", type(e).__name__, e)
