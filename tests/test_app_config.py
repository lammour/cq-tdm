"""Application settings: tolerant loading and atomic saving."""

import json

from cq_tdm.core.app_config import AppConfig
from cq_tdm.core.utils import atomic_write_json


def test_round_trip(tmp_path):
    config = AppConfig(report_logo_scale=0.6, theme="light", last_performed_by="A. Martin")
    config.save()
    assert AppConfig.config_path() == tmp_path / "config" / "settings.json"  # conftest redirection
    loaded = AppConfig.load()
    assert (loaded.report_logo_scale, loaded.theme, loaded.last_performed_by) == (0.6, "light", "A. Martin")


def test_values_of_the_wrong_type_fall_back_to_defaults():
    """A hand-edited file must not surface later as a TypeError in a dialog."""
    AppConfig.config_path().write_text(json.dumps({
        "report_logo_scale": "40 %",
        "theme": 5,
        "report_include_history": "oui",
        "device_database_path": "/data/devices.json",
        "last_device_id": None,
        "unknown_key": 1,
    }), encoding="utf-8")
    loaded = AppConfig.load()
    defaults = AppConfig()
    assert loaded.report_logo_scale == defaults.report_logo_scale
    assert loaded.theme == "dark"
    assert loaded.report_include_history is False
    assert loaded.device_database_path == "/data/devices.json"  # valid values are kept
    assert loaded.last_device_id == ""


def test_out_of_range_values_are_clamped():
    AppConfig.config_path().write_text(json.dumps({"report_logo_scale": 7, "theme": "blue"}),
                                       encoding="utf-8")
    loaded = AppConfig.load()
    assert loaded.report_logo_scale == 1.0 and loaded.theme == "dark"


def test_unreadable_file_is_set_aside():
    path = AppConfig.config_path()
    path.write_text("{ not json", encoding="utf-8")
    loaded = AppConfig.load()
    assert loaded == AppConfig()
    assert path.with_suffix(".json.bak").read_text(encoding="utf-8") == "{ not json"


def test_atomic_write_keeps_the_previous_file_on_failure(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    target = folder / "data.json"
    atomic_write_json(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}

    class Unserialisable:
        pass

    try:
        atomic_write_json(target, {"a": Unserialisable()})
    except TypeError:
        pass
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}  # untouched
    assert [p.name for p in folder.iterdir()] == ["data.json"]  # no temporary file left
