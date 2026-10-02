"""CSV export of the history: French by default, English on request."""

import csv
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cq_tdm.core.app_config import get_app_config  # noqa: E402
from cq_tdm.core.qc_history import QCRun  # noqa: E402
from cq_tdm.gui import history_panel as hp  # noqa: E402
from cq_tdm.gui import main_window as mw  # noqa: E402

HEADER = ["date", "kV", "mAs", "ct_eau_UH", "uniformite_UH", "bruit_UH", "bruit_ref_UH",
          "f_spb_cmm", "f_spb_ref_cmm", "artefacts", "statut",
          "action_corrective_date", "action_corrective", "notes", "pdf"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _export(tmp_path, monkeypatch, runs) -> str:
    panel = hp.HistoryPanel()
    panel.set_runs(runs, None, None)
    path = tmp_path / "historique.csv"
    monkeypatch.setattr(hp.QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    panel._export_csv()
    return path.read_text(encoding="utf-8-sig")


def _runs():
    return [
        QCRun(run_date="2026-09-15", series_uid="A", kvp=120.0, mas=280.0, water_ct=-1.9,
              uniformity=0.3, noise=5.4333540167467635, ref_noise=5.43,
              nps_freq=0.24918720924687834, ref_nps_freq=0.249, artifacts_present=False,
              notes="écart <2 UH ; fantôme recentré"),
        QCRun(run_date="2026-06-01", series_uid="B", kvp=120.0, mas=280.0, water_ct=1.25,
              uniformity=1.0, noise=None, nps_freq=None),
    ]


def test_french_format_by_default(qapp, tmp_path, monkeypatch):
    text = _export(tmp_path, monkeypatch, _runs())
    rows = list(csv.reader(text.splitlines(), delimiter=";"))
    assert rows[0] == HEADER
    # Oldest first; values not measured are empty, not "None"
    assert rows[1][:9] == ["2026-06-01", "120", "280", "1,2", "1,0", "", "", "", ""]
    # Decimal comma, rounded as on screen
    assert rows[2][:9] == ["2026-09-15", "120", "280", "-1,9", "0,3", "5,43", "5,43", "0,249", "0,249"]
    assert rows[2][13] == "écart <2 UH ; fantôme recentré"  # ";" in a note stays in its cell
    assert "." not in "".join(rows[2][1:9])


def test_english_format_on_request(qapp, tmp_path, monkeypatch):
    get_app_config().csv_format = "en"
    text = _export(tmp_path, monkeypatch, _runs())
    rows = list(csv.reader(text.splitlines(), delimiter=","))
    assert rows[0] == HEADER
    assert rows[2][:9] == ["2026-09-15", "120", "280", "-1.9", "0.3", "5.43", "5.43", "0.249", "0.249"]
    assert rows[2][13] == "écart <2 UH ; fantôme recentré"


def test_configuration_menu_switches_the_format(qapp, monkeypatch):
    monkeypatch.setattr(mw, "save_app_config", lambda: None)
    window = mw.MainWindow()
    action = window._csv_english_action
    assert not action.isChecked() and get_app_config().csv_format == "fr"
    action.trigger()
    assert get_app_config().csv_format == "en"
    action.trigger()
    assert get_app_config().csv_format == "fr"
