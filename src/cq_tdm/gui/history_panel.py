"""Results history panel: table of recorded QC runs plus a trend chart.

The panel is a view: it never writes to the database itself. Deleting a run,
promoting one to reference or re-linking a PDF are emitted as signals that the
main window applies and persists.
"""

import csv
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.app_config import get_app_config
from ..core.qc_history import METRICS, NC, NCG, OK, STATUS_SHORT, QCRun, evaluate_run
from ..core.trend_chart import DARK_PALETTE, LIGHT_PALETTE, render_trend_chart
from ..core.utils import format_fr

_COLUMNS = ["Date", "kV", "mAs", "CT eau", "Unif.", "Bruit σ", "f SPB", "Artéfacts", "Statut"]
_HIT_RADIUS_PX = 10


def _palette() -> dict:
    return LIGHT_PALETTE if get_app_config().theme == "light" else DARK_PALETTE


class _ChartLabel(QLabel):
    """QLabel showing the rendered chart; reports clicks and hovers."""

    clicked = Signal(QPoint)
    hovered = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.hovered.emit(event.position().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        super().leaveEvent(event)


class HistoryPanel(QWidget):
    """Table of past QC runs plus a trend chart for one metric."""

    reference_requested = Signal(object)  # QCRun
    delete_requested = Signal(object)  # QCRun
    pdf_relinked = Signal(object, str)  # QCRun, new path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._runs: list[QCRun] = []
        self._current: QCRun | None = None
        self._ref_noise: float | None = None
        self._ref_nps: float | None = None
        self._point_hits: list[tuple[float, float, QCRun]] = []

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(120)
        self._redraw_timer.timeout.connect(self._draw_chart)

        self._setup_ui()

    # -- construction -------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._summary = QLabel("Aucune installation sélectionnée")
        self._summary.setStyleSheet("color: #888;")
        layout.addWidget(self._summary)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(lambda _item: self._show_details())
        splitter.addWidget(self._table)

        chart_box = QWidget()
        chart_layout = QVBoxLayout(chart_box)
        chart_layout.setContentsMargins(0, 4, 0, 0)

        metric_row = QHBoxLayout()
        metric_row.addWidget(QLabel("Tendance :"))
        self._metric_combo = QComboBox()
        for key, label in METRICS:
            self._metric_combo.addItem(label, key)
        self._metric_combo.setCurrentIndex(2)  # noise
        self._metric_combo.currentIndexChanged.connect(lambda _i: self._schedule_redraw())
        metric_row.addWidget(self._metric_combo, 1)
        chart_layout.addLayout(metric_row)

        self._chart = _ChartLabel()
        self._chart.clicked.connect(self._on_chart_clicked)
        self._chart.hovered.connect(self._on_chart_hovered)
        # Ignored: the pixmap is re-rendered to fit the label, it must not drive the layout
        self._chart.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._chart.setMinimumSize(200, 170)
        self._chart.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chart_layout.addWidget(self._chart, 1)
        splitter.addWidget(chart_box)
        splitter.setSizes([220, 260])

        btn_row = QHBoxLayout()
        self._btn_ref = QPushButton("Définir comme référence")
        self._btn_ref.setToolTip("Utiliser le bruit et la fréquence SPB de ce contrôle comme valeurs de référence")
        self._btn_ref.clicked.connect(self._promote_to_reference)
        self._btn_pdf = QPushButton("Ouvrir le PDF")
        self._btn_pdf.clicked.connect(self._open_pdf)
        self._btn_delete = QPushButton("Supprimer")
        self._btn_delete.clicked.connect(self._delete_selected)
        self._btn_csv = QPushButton("Exporter CSV")
        self._btn_csv.clicked.connect(self._export_csv)
        for b in (self._btn_ref, self._btn_pdf, self._btn_delete):
            b.setEnabled(False)
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        btn_row.addWidget(self._btn_csv)
        layout.addLayout(btn_row)

    # -- public API ---------------------------------------------------------

    def set_runs(self, runs: list[QCRun], ref_noise: float | None, ref_nps: float | None):
        """Replace the history and the reference values used for the bands."""
        self._runs = sorted(runs, key=lambda r: (r.date, r.recorded_at), reverse=True)
        self._ref_noise, self._ref_nps = ref_noise, ref_nps
        self._refresh()

    def set_references(self, ref_noise: float | None, ref_nps: float | None):
        """Update the reference values used for the chart band and the current run."""
        self._ref_noise, self._ref_nps = ref_noise, ref_nps
        if self._current is not None:
            self._current.ref_noise, self._current.ref_nps_freq = ref_noise, ref_nps
        self._refresh()

    def set_current_run(self, run: QCRun | None):
        """Show (or hide) the measurement in progress, distinct from recorded runs."""
        if run is not None:
            run.is_current = True
            run.ref_noise, run.ref_nps_freq = self._ref_noise, self._ref_nps
        self._current = run
        self._refresh()

    def select_run(self, run: QCRun | None):
        rows = self._all_rows()
        if run in rows:
            self._table.selectRow(rows.index(run))

    # -- table --------------------------------------------------------------

    def _all_rows(self) -> list[QCRun]:
        rows = list(self._runs)
        if self._current is not None:
            rows.insert(0, self._current)
        return rows

    def _refresh(self):
        pal = _palette()
        rows = self._all_rows()
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))

        for r, run in enumerate(rows):
            st = evaluate_run(run)
            cells = [
                ("En cours" if run.is_current else run.date_fr(), None),
                (format_fr(run.kvp, 0) if run.kvp else "—", None),
                (format_fr(run.mas, 0) if run.mas else "—", None),
                (format_fr(run.water_ct, 1, sign=True), st["water_ct"]),
                (format_fr(run.uniformity, 1), st["uniformity"]),
                (format_fr(run.noise, 2), st["noise"]),
                ("—" if run.nps_freq is None else format_fr(run.nps_freq, 3), st["nps_freq"]),
                ({None: "—", False: "Absents", True: "Présents"}[run.artifacts_present], st["artifacts"]),
                (STATUS_SHORT[st["overall"]], st["overall"]),
            ]
            last = len(cells) - 1
            for c, (text, status) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if status in (NC, NCG):
                    item.setForeground(QColor(pal[status]))
                    if c == last:
                        f = item.font(); f.setBold(True); item.setFont(f)
                elif status == OK and c == last:
                    item.setForeground(QColor(pal["ok"]))
                if run.is_current:
                    f = item.font(); f.setItalic(True); item.setFont(f)
                if run.notes:
                    item.setToolTip(run.notes)
                self._table.setItem(r, c, item)

        n = len(self._runs)
        if n == 0:
            self._summary.setText("Aucun contrôle enregistré pour cette installation")
        else:
            statuses = [evaluate_run(r)["overall"] for r in self._runs]
            nc = statuses.count(NC) + statuses.count(NCG)
            first = self._runs[-1].date.strftime("%m/%Y")
            self._summary.setText(
                f"{n} contrôle{'s' if n > 1 else ''} depuis {first} · {nc} non conforme{'s' if nc > 1 else ''} · "
                f"dernier : {self._runs[0].date_fr()}"
            )
        self._on_selection_changed()

    def _selected_run(self) -> QCRun | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        all_rows = self._all_rows()
        return all_rows[rows[0].row()] if rows[0].row() < len(all_rows) else None

    def _on_selection_changed(self):
        run = self._selected_run()
        recorded = run is not None and not run.is_current
        self._btn_ref.setEnabled(run is not None and run.nps_freq is not None)
        self._btn_delete.setEnabled(recorded)
        if recorded and run.pdf_path:
            exists = Path(run.pdf_path).is_file()
            self._btn_pdf.setEnabled(True)
            self._btn_pdf.setText("Ouvrir le PDF" if exists else "Localiser le PDF…")
            self._btn_pdf.setToolTip(run.pdf_path if exists else f"Fichier introuvable :\n{run.pdf_path}")
        else:
            self._btn_pdf.setEnabled(False)
            self._btn_pdf.setText("Ouvrir le PDF")
            self._btn_pdf.setToolTip("")
        self._schedule_redraw()

    # -- actions ------------------------------------------------------------

    def _promote_to_reference(self):
        run = self._selected_run()
        if run is not None:
            self.reference_requested.emit(run)

    def _open_pdf(self):
        run = self._selected_run()
        if run is None or not run.pdf_path:
            return
        path = Path(run.pdf_path)
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            return
        new_path, _ = QFileDialog.getOpenFileName(
            self, "Localiser le rapport PDF", str(path.parent if path.parent.exists() else Path.home()),
            "PDF (*.pdf)")
        if new_path:
            self.pdf_relinked.emit(run, new_path)
            self._on_selection_changed()

    def _delete_selected(self):
        run = self._selected_run()
        if run is None or run.is_current:
            return
        answer = QMessageBox.question(
            self, "Supprimer le contrôle",
            f"Supprimer le contrôle du {run.date_fr()} de l'historique ?\n"
            "Le rapport PDF n'est pas supprimé.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_requested.emit(run)

    def _show_details(self):
        run = self._selected_run()
        if run is None:
            return
        st = evaluate_run(run)
        ref_n = "—" if run.ref_noise is None else format_fr(run.ref_noise, 2)
        ref_f = "—" if run.ref_nps_freq is None else format_fr(run.ref_nps_freq, 3)
        nps = "—" if run.nps_freq is None else format_fr(run.nps_freq, 3)
        lines = [
            "<b>Mesure en cours (non enregistrée)</b>" if run.is_current
            else f"<b>Contrôle du {run.date_fr()}</b>",
            f"{format_fr(run.kvp, 0)} kV · {format_fr(run.mas, 0)} mAs"
            + (f" · {format_fr(run.slice_thickness, 1)} mm" if run.slice_thickness else "")
            + (f" · {run.kernel}" if run.kernel else ""),
            "",
            f"Nombre CT de l'eau : {format_fr(run.water_ct, 1, sign=True)} HU — {STATUS_SHORT[st['water_ct']]}",
            f"Uniformité : {format_fr(run.uniformity, 1)} HU — {STATUS_SHORT[st['uniformity']]}",
            f"Bruit σ : {format_fr(run.noise, 2)} HU (réf. {ref_n}) — {STATUS_SHORT[st['noise']]}",
            f"Fréq. SPB : {nps} c/mm (réf. {ref_f}) — {STATUS_SHORT[st['nps_freq']]}",
            f"Artéfacts : {STATUS_SHORT[st['artifacts']]}"
            + (f" — {run.artifacts_description}" if run.artifacts_description else ""),
        ]
        if run.hu_slice_index is not None:
            lines.append(f"Coupe UH {run.hu_slice_index + 1}"
                         + (f" · SPB {run.nps_start_slice + 1}–{run.nps_end_slice + 1}"
                            if run.nps_start_slice is not None and run.nps_end_slice is not None else ""))
        if run.notes:
            lines += ["", f"<i>{run.notes}</i>"]
        if run.pdf_path:
            lines += ["", f"Rapport : {run.pdf_path}"]
        if run.software_version:
            lines.append(f"CQ TDM {run.software_version}")
        QMessageBox.information(self, "Détail du contrôle", "<br>".join(lines))

    def _export_csv(self):
        if not self._runs:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exporter l'historique", "historique_cq.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["date", "kV", "mAs", "ct_eau_HU", "uniformite_HU", "bruit_HU", "bruit_ref_HU",
                        "f_spb_cmm", "f_spb_ref_cmm", "artefacts", "statut", "notes", "pdf"])
            for run in sorted(self._runs, key=lambda r: r.date):
                st = evaluate_run(run)
                w.writerow([
                    run.run_date, run.kvp, run.mas, run.water_ct, run.uniformity,
                    run.noise, "" if run.ref_noise is None else run.ref_noise,
                    "" if run.nps_freq is None else run.nps_freq,
                    "" if run.ref_nps_freq is None else run.ref_nps_freq,
                    "" if run.artifacts_present is None else int(run.artifacts_present),
                    STATUS_SHORT[st["overall"]], run.notes, run.pdf_path,
                ])

    # -- chart --------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_redraw()

    def _schedule_redraw(self):
        self._redraw_timer.start()

    def _draw_chart(self):
        chart = render_trend_chart(
            self._runs, self._metric_combo.currentData(), self._ref_noise, self._ref_nps,
            current=self._current, selected=self._selected_run(), palette=_palette(),
            width_px=max(self._chart.width(), 200), height_px=max(self._chart.height(), 140),
        )
        self._point_hits = chart.hits
        pix = QPixmap()
        pix.loadFromData(chart.png)
        self._chart.setPixmap(pix)

    def _run_at(self, pos: QPoint) -> QCRun | None:
        pix = self._chart.pixmap()
        if pix is None or pix.isNull() or not self._point_hits:
            return None
        ox = (self._chart.width() - pix.width()) / 2
        oy = (self._chart.height() - pix.height()) / 2
        x, y = pos.x() - ox, pos.y() - oy
        best, best_d2 = None, _HIT_RADIUS_PX ** 2
        for px, py, run in self._point_hits:
            d2 = (px - x) ** 2 + (py - y) ** 2
            if d2 <= best_d2:
                best, best_d2 = run, d2
        return best

    def _on_chart_clicked(self, pos: QPoint):
        run = self._run_at(pos)
        if run is None:
            return
        row = self._all_rows().index(run)
        self._table.selectRow(row)
        self._table.scrollToItem(self._table.item(row, 0))

    def _on_chart_hovered(self, pos: QPoint):
        run = self._run_at(pos)
        if run is None:
            self._chart.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self._chart.setToolTip("")
            return
        metric = self._metric_combo.currentData()
        decimals = 3 if metric == "nps_freq" else 2
        when = "Mesure en cours" if run.is_current else run.date_fr()
        self._chart.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._chart.setToolTip(f"{when}\n{dict(METRICS)[metric]} : {format_fr(getattr(run, metric), decimals)}")
