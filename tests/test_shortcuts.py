"""Keyboard shortcuts of the main window."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cq_tdm.gui import main_window as mw  # noqa: E402

from .dicom_factory import write_series  # noqa: E402

SHIFT = Qt.KeyboardModifier.ShiftModifier
KEYPAD = Qt.KeyboardModifier.KeypadModifier
NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(mw, "save_app_config", lambda: None)
    window = mw.MainWindow()
    write_series(tmp_path / "dicom", 12)
    assert window._load_dicom_folder(str(tmp_path / "dicom"))
    window.show()
    window.activateWindow()
    QTest.qWaitForWindowActive(window)
    yield window
    for timer in window.findChildren(mw.QTimer) + [
            window._hu_debounce_timer, window._nps_debounce_timer, window._ref_debounce_timer]:
        timer.stop()
    window.close()


def _zoom(window) -> int:
    return window.image_viewer.viewer.get_zoom_percent()


@pytest.mark.parametrize("key, modifier", [
    (Qt.Key.Key_Equal, NONE),   # "=" unshifted, QWERTY and AZERTY
    (Qt.Key.Key_Plus, SHIFT),   # "+" is Shift + "=" on both layouts
    (Qt.Key.Key_Plus, KEYPAD),  # numeric keypad
])
def test_zoom_in_keys(window, key, modifier):
    before = _zoom(window)
    QTest.keyClick(window, key, modifier)
    assert _zoom(window) > before


@pytest.mark.parametrize("key, modifier", [
    (Qt.Key.Key_Minus, NONE),        # "-" unshifted on QWERTY, and on the "6" key of AZERTY
    (Qt.Key.Key_Underscore, SHIFT),  # Shift + "-" on QWERTY
    (Qt.Key.Key_Underscore, NONE),   # "_" unshifted on AZERTY
    (Qt.Key.Key_6, SHIFT),           # Shift + the "-" key on AZERTY
    (Qt.Key.Key_6, NONE),
    (Qt.Key.Key_Minus, KEYPAD),      # numeric keypad
])
def test_zoom_out_keys(window, key, modifier):
    before = _zoom(window)
    QTest.keyClick(window, key, modifier)
    assert _zoom(window) < before


def test_zoom_keys_step_like_the_wheel_and_come_back(window):
    start = _zoom(window)
    QTest.keyClick(window, Qt.Key.Key_Plus, SHIFT)
    assert _zoom(window) == pytest.approx(start * 1.15, abs=1)
    QTest.keyClick(window, Qt.Key.Key_Minus)
    assert _zoom(window) == pytest.approx(start, abs=1)


def test_typing_in_a_field_does_not_zoom(window):
    """"6" and "-" typed in a number field are text, not zoom-out."""
    spin = window.image_viewer.level_spin
    spin.setFocus()
    spin.selectAll()
    before = _zoom(window)
    QTest.keyClicks(spin, "-60")
    assert spin.value() == -60
    assert _zoom(window) == before


def test_no_single_letter_shortcut_is_left(window):
    """H, N, F, R, U, S, I, A: a stray key press must not move the analysis slices."""
    sequences = {s.key().toString() for s in window.findChildren(QShortcut)}
    assert not {"H", "N", "F", "R", "U", "S", "I", "A"} & sequences
    hu, nps = window.image_viewer.get_hu_slice_index(), window.image_viewer.get_nps_slice_range()
    window.image_viewer.set_hu_slice_index(2)
    for letter in "HNFRUSIA":
        QTest.keyClick(window, QKeySequence(letter)[0].key())
    assert window.image_viewer.get_hu_slice_index() == 2 != hu
    assert window.image_viewer.get_nps_slice_range() == nps


def test_slice_navigation_keys_are_kept(window):
    slider = window.image_viewer.slice_slider
    slider.setValue(5)
    QTest.keyClick(window, Qt.Key.Key_Right)
    assert slider.value() == 6
    QTest.keyClick(window, Qt.Key.Key_Home)
    assert slider.value() == 0
    QTest.keyClick(window, Qt.Key.Key_End)
    assert slider.value() == 11


def test_zoom_keys_work_wherever_the_focus_is_outside_a_field(window):
    for widget in (window.image_viewer.viewer, window.image_viewer.slice_slider, window.btn_export):
        widget.setFocus()
        before = _zoom(window)
        QTest.keyClick(widget, Qt.Key.Key_Equal)
        assert _zoom(window) > before, widget


def test_ctrl_combinations_are_not_zoom_keys(window):
    before = _zoom(window)
    QTest.keyClick(window, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
    assert _zoom(window) == before
