"""PDF report generation for CT quality control results.

Generates structured PDF reports according to ANSM decision of 18/12/2025.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional
from io import BytesIO

from cq_tdm import __version__

from ..core import DicomImage, WaterPhantomResults, NPSResult, format_fr
from ..core.qc_history import (
    INCOMPLETE, NC, NC_OR_NCG, NC_OR_NCG_DETAIL, NC_OR_NCG_NOTICE, NCG, OK, PENDING, STATUS_SHORT,
    DEFAULT_CONTROL_TYPE, UNKNOWN_DATE, QCRun, artifacts_status, dicom_date_to_iso,
    evaluate_measurements, evaluate_run, iso_to_fr,
    noise_bounds, noise_status, nps_bounds, nps_status, pending_reasons, uniformity_status,
    water_ct_status,
)
from ..core.trend_chart import LIGHT_PALETTE, render_trend_chart

logger = logging.getLogger(__name__)

# Reportlab imports are deferred to _ensure_reportlab() for faster startup
colors = None
A4 = None
getSampleStyleSheet = None
ParagraphStyle = None
mm = None
cm = None
SimpleDocTemplate = None
Paragraph = None
Spacer = None
Table = None
TableStyle = None
Image = None
PageBreak = None
KeepTogether = None
TA_CENTER = None
TA_LEFT = None
TA_RIGHT = None
pdfmetrics = None
TTFont = None
canvas = None


def _ensure_reportlab():
    """Import reportlab modules on first use."""
    global colors, A4, getSampleStyleSheet, ParagraphStyle, mm, cm
    global SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
    global TA_CENTER, TA_LEFT, TA_RIGHT, pdfmetrics, TTFont, canvas
    if colors is not None:
        return
    from reportlab.lib import colors as _colors
    from reportlab.lib.pagesizes import A4 as _A4
    from reportlab.lib.styles import getSampleStyleSheet as _gss, ParagraphStyle as _PS
    from reportlab.lib.units import mm as _mm, cm as _cm
    from reportlab.platypus import (
        SimpleDocTemplate as _SD, Paragraph as _P, Spacer as _Sp,
        Table as _T, TableStyle as _TS, Image as _I, PageBreak as _PB, KeepTogether as _KT,
    )
    from reportlab.lib.enums import TA_CENTER as _TC, TA_LEFT as _TL, TA_RIGHT as _TR
    from reportlab.pdfbase import pdfmetrics as _pm
    from reportlab.pdfbase.ttfonts import TTFont as _TF
    from reportlab.pdfgen import canvas as _cv
    colors = _colors
    A4 = _A4
    getSampleStyleSheet = _gss
    ParagraphStyle = _PS
    mm = _mm
    cm = _cm
    SimpleDocTemplate = _SD
    Paragraph = _P
    Spacer = _Sp
    Table = _T
    TableStyle = _TS
    Image = _I
    PageBreak = _PB
    KeepTogether = _KT
    TA_CENTER = _TC
    TA_LEFT = _TL
    TA_RIGHT = _TR
    pdfmetrics = _pm
    TTFont = _TF
    canvas = _cv


# Same ROI colours as on screen (MainWindow._display_rois / _display_nps_roi)
ROI_COLOR_CENTRAL = '#ffff00'  # yellow
ROI_COLOR_PERIPHERAL = '#00ffff'  # cyan
ROI_COLOR_NPS = '#00ff00'  # green
ROI_COLOR_NPS_SKIPPED = '#ff5050'  # SPB ROI clipped by the image border, not measured

# Labels drawn on the ROI images and repeated in the ROI table
HU_ROI_LABELS = ("C", "12h", "3h", "6h", "9h")
HU_ROI_NAMES = ("centre", "haut", "droite", "bas", "gauche")
NPS_ROI_NAMES = ("haut-gauche", "bas-droite", "bas-gauche", "haut-droite", "haut", "bas", "gauche", "droite")

# Wording of the decision for each judged status, with a symbol that survives a
# monochrome print. The standard PDF fonts have no ✔ / ✘: reportlab takes them
# from ZapfDingbats (as it takes ≤ and → from Symbol), which PDF viewers provide.
_VERDICT = {
    OK: ("✔", "Conforme", 'ResultOK'),
    NC: ("✘", "Non conforme", 'ResultNC'),
    NCG: ("✘", "Non-conformité grave", 'ResultNCG'),
    NC_OR_NCG: ("✘", NC_OR_NCG_NOTICE, 'ResultNCG'),
}

# A pending test, as worded in the summary table
NOT_DONE = "Non réalisé"
NO_REFERENCE = "Non évalué — référence absente"


@dataclass
class ArtifactInspectionResult:
    """Result from artifact inspection."""
    artifacts_present: bool  # True if artifacts detected (NC)
    description: str = ""  # Description of artifacts if present


_NBSP = "\N{NO-BREAK SPACE}"


def _focal_spots_text(raw: str) -> str:
    """Focal spot size(s) as printed: "1,2 mm" or "0,6 / 1,2 mm" instead of DICOM's "1.200000"."""
    values = []
    for part in (raw or "").split("/"):
        try:
            values.append(f"{float(part):g}".replace(".", ","))
        except ValueError:
            return raw or "—"
    return " / ".join(values) + " mm" if values else "—"


def _fr(text: str) -> str:
    """French typography: no line break before : ; ! ? % », after «, or between a number and its unit."""
    text = re.sub(r' (?=[:;!?%»])', _NBSP, text).replace('« ', '«' + _NBSP)
    text = re.sub(r'(?<=\d) (?=(?:UH|mm|mGy|cycles/mm)\b)', _NBSP, text)
    return text.replace('z = ', f'z{_NBSP}={_NBSP}')


def _verdict(status: str, upper: bool = False) -> tuple[str, str]:
    """(verdict with its symbol, paragraph style) for a judged status."""
    symbol, text, style = _VERDICT[status]
    if upper and status != NC_OR_NCG:
        text = text.upper()
    return f"{symbol} {text}", style


def _action_text(status: str) -> str:
    """Action required by the decision for a status ("" when there is none)."""
    if status == NCG:
        return "Arrêt de l'exploitation et signalement à l'ANSM et à l'ARS dont dépend l'exploitant dans un délai de 2 jours ouvrés dans le cadre du système national de matériovigilance"
    if status == NC_OR_NCG:
        return NC_OR_NCG_DETAIL
    if status == NC:
        return "Remise en conformité dès que possible"
    return ""


def _status_color(status: str) -> colors.Color:
    """Get color for status."""
    _ensure_reportlab()
    if status == OK:
        return colors.Color(0, 0.5, 0)
    if status in (NCG, NC_OR_NCG):
        return colors.HexColor('#c62828')
    if status == NC:
        # Dark orange: colors.orange is unreadable on white and vanishes in greyscale
        return colors.HexColor('#b45309')
    return colors.Color(0.4, 0.4, 0.4)


def _hu_slice_text(image: DicomImage, hu_slice_index: Optional[int], total_slices: Optional[int]) -> str:
    """Slice of the CT number, uniformity and central σ measurements: "12 / 24 (z = +5,0 mm)"."""
    z = f"z = {format_fr(image.slice_location, 1, sign=True)} mm"
    if hu_slice_index is None:
        return z
    number = str(hu_slice_index + 1)
    if total_slices:
        number += f" / {total_slices}"
    return f"{number} ({z})"


def _nps_slice_text(nps_results: NPSResult, nps_slice_range: Optional[tuple[int, int]]) -> str:
    """Slices of the SPB and noise measurements: "8 à 17 (10 coupes, z = -22,5 à +22,5 mm)"."""
    count = nps_results.num_slices
    detail = f"{count} coupe{'s' if count > 1 else ''}"
    config = nps_results.roi_config
    detail += (f", z = {format_fr(config.slice_start_mm, 1, sign=True)} "
               f"à {format_fr(config.slice_end_mm, 1, sign=True)} mm")
    if nps_slice_range is None:
        return detail
    return f"{nps_slice_range[0] + 1} à {nps_slice_range[1] + 1} ({detail})"


def _slice_figure(image: DicomImage, window: int, level: int, size_cm: float):
    """Square figure filled edge to edge by a windowed slice.

    The figure has the size it is printed at, so font sizes and line widths
    are the printed ones.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    side = size_cm / 2.54
    fig = plt.figure(figsize=(side, side), dpi=200)
    try:
        ax = fig.add_axes([0, 0, 1, 1])
        ax.imshow(image.pixel_array, cmap='gray', aspect='equal',
                  vmin=level - window / 2, vmax=level + window / 2)
        ax.axis('off')
    except Exception:
        plt.close(fig)
        raise
    return fig, ax


def _figure_png(fig) -> BytesIO:
    """PNG of a figure, at the figure's own size."""
    buffer = BytesIO()
    fig.savefig(buffer, format='PNG', facecolor='white')
    buffer.seek(0)
    return buffer


def _close_figure(fig):
    import matplotlib.pyplot as plt
    plt.close(fig)


def _roi_label(ax, x: float, y: float, text: str, color: str, ha: str = 'center', va: str = 'center'):
    """ROI label on a dark background, readable over water and over the phantom wall."""
    ax.text(x, y, text, color=color, fontsize=6.5, fontweight='bold', ha=ha, va=va,
            bbox=dict(facecolor='black', alpha=0.6, edgecolor='none', pad=1.2))


def _generate_nps_plot(nps_result: NPSResult, width_cm: float = 8.7, height_cm: float = 5.8) -> BytesIO:
    """Generate NPS radial curve plot as PNG in a BytesIO buffer."""
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt

    # Figure at its printed size: the font sizes below are the printed ones
    fig, ax = plt.subplots(figsize=(width_cm / 2.54, height_cm / 2.54), dpi=200)
    try:
        # Plot radial NPS
        ax.plot(
            nps_result.frequencies_radial,
            nps_result.nps_radial,
            'b-',
            linewidth=1.2,
            label='SPB radial'
        )

        # Mark mean frequency
        ax.axvline(
            nps_result.mean_frequency,
            color='r',
            linestyle='--',
            linewidth=0.9,
            label=_fr(f'Fréquence moyenne : {format_fr(nps_result.mean_frequency, 3)} cycles/mm')
        )

        ax.set_xlabel('Fréquence (cycles/mm)', fontsize=7.5)
        ax.set_ylabel('SPB (UH²·mm²)', fontsize=7.5)
        ax.tick_params(labelsize=7)
        ax.legend(loc='upper right', fontsize=6.5)
        ax.grid(True, alpha=0.3)
        ax.set_xlim(left=0)
        ax.set_ylim(bottom=0)

        fig.tight_layout(pad=0.4)
        return _figure_png(fig)
    finally:
        plt.close(fig)


def _generate_hu_roi_image(
    image: DicomImage,
    water_results: WaterPhantomResults,
    window: int = 400,
    level: int = 40,
    size_cm: float = 6.5,
) -> BytesIO:
    """Generate image with labelled HU ROI overlays as PNG in a BytesIO buffer."""
    from matplotlib.patches import Circle

    fig, ax = _slice_figure(image, window, level, size_cm)
    try:
        measurements = (water_results.central, water_results.top, water_results.right,
                        water_results.bottom, water_results.left)
        for i, (m, label) in enumerate(zip(measurements, HU_ROI_LABELS)):
            color = ROI_COLOR_CENTRAL if i == 0 else ROI_COLOR_PERIPHERAL
            ax.add_patch(Circle((m.center_col, m.center_row), m.radius,
                                fill=False, edgecolor=color, linewidth=1.2))

        # "C" inside the central ROI; peripheral labels on the side of the
        # phantom centre so they never fall on the wall
        gap = 0.02 * image.pixel_array.shape[1]
        c, top, right, bottom, left = measurements
        _roi_label(ax, c.center_col, c.center_row - c.radius + gap, "C", ROI_COLOR_CENTRAL, va='top')
        _roi_label(ax, top.center_col, top.center_row + top.radius + gap, "12h", ROI_COLOR_PERIPHERAL, va='top')
        _roi_label(ax, bottom.center_col, bottom.center_row - bottom.radius - gap, "6h",
                   ROI_COLOR_PERIPHERAL, va='bottom')
        _roi_label(ax, right.center_col - right.radius - gap, right.center_row, "3h",
                   ROI_COLOR_PERIPHERAL, ha='right')
        _roi_label(ax, left.center_col + left.radius + gap, left.center_row, "9h",
                   ROI_COLOR_PERIPHERAL, ha='left')

        return _figure_png(fig)
    finally:
        _close_figure(fig)


def _generate_nps_roi_image(
    image: DicomImage,
    nps_results: NPSResult,
    window: int = 400,
    level: int = 40,
    size_cm: float = 6.5,
) -> BytesIO:
    """Generate image with numbered NPS ROI overlays as PNG in a BytesIO buffer."""
    from matplotlib.patches import Rectangle

    fig, ax = _slice_figure(image, window, level, size_cm)
    try:
        # ROIs clipped by the image border were not measured: red, as on screen
        skipped = set(nps_results.skipped_rois)
        for i, roi_pos in enumerate(nps_results.roi_config.rois):
            color = ROI_COLOR_NPS_SKIPPED if i in skipped else ROI_COLOR_NPS
            half_size = roi_pos.side_square / 2
            ax.add_patch(Rectangle(
                (roi_pos.x - half_size, roi_pos.y - half_size),
                roi_pos.side_square,
                roi_pos.side_square,
                fill=False,
                edgecolor=color,
                linewidth=1.2,
            ))
            _roi_label(ax, roi_pos.x, roi_pos.y, str(i + 1), color)

        return _figure_png(fig)
    finally:
        _close_figure(fig)


def _generate_artifact_image(
    image: DicomImage,
    window: int = 80,
    level: int = 0,
    size_cm: float = 9.0,
) -> BytesIO:
    """Generate image with ANSM artifact inspection window settings as PNG.

    ANSM requires: Window center (L) = 0 HU, Window width (W) = 80 HU
    """
    fig, _ax = _slice_figure(image, window, level, size_cm)
    try:
        return _figure_png(fig)
    finally:
        _close_figure(fig)


def _create_status_badge(status_text: str, status_color: colors.Color) -> Table:
    """Create a colored status badge as a small table."""
    _ensure_reportlab()
    # Always use white text for badges
    text_color = colors.white

    # Adjust width based on text length
    badge_width = max(4 * cm, len(status_text) * 0.28 * cm)

    data = [[status_text]]
    table = Table(data, colWidths=[badge_width])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), status_color),
        ('TEXTCOLOR', (0, 0), (0, 0), text_color),
        ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (0, 0), 10),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
        ('VALIGN', (0, 0), (0, 0), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (0, 0), 4),
        ('BOTTOMPADDING', (0, 0), (0, 0), 4),
        ('LEFTPADDING', (0, 0), (0, 0), 8),
        ('RIGHTPADDING', (0, 0), (0, 0), 8),
        ('ROUNDEDCORNERS', [3, 3, 3, 3]),
    ]))
    return table


def _fit_text(text: str, font: str, size: float, max_width: float) -> str:
    """Text cut with "…" so that it is at most ``max_width`` wide."""
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text
    while text and pdfmetrics.stringWidth(text + "…", font, size) > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


def _draw_footer(canvas_obj: canvas.Canvas, text: str):
    """Draw the footer line, centered at the bottom of the page."""
    _ensure_reportlab()
    canvas_obj.saveState()
    canvas_obj.setFont('Helvetica', 8)
    canvas_obj.setFillColor(colors.Color(0.4, 0.4, 0.4))
    canvas_obj.drawCentredString(A4[0] / 2, 1.2 * cm, text)
    canvas_obj.restoreState()


def _numbered_canvas(footer_text: str):
    """Canvas class writing "<footer_text> · page n / N" at the bottom of every page.

    The page count is only known at the end: pages are kept instead of being
    emitted, then replayed with their footer (two passes).
    """
    _ensure_reportlab()

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_pages = []

        def showPage(self):
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_pages)
            for state in self._saved_pages:
                self.__dict__.update(state)
                _draw_footer(self, f"{footer_text} · page {self.getPageNumber()} / {total}")
                super().showPage()
            super().save()

    return NumberedCanvas


def _draw_header(canvas_obj: canvas.Canvas, hospital_name: str, device_name: str, inventory_number: str, control_datetime: str):
    """Draw header with hospital name, device name with inventory number, and control date."""
    _ensure_reportlab()
    canvas_obj.saveState()
    font, size = 'Helvetica', 8
    canvas_obj.setFont(font, size)
    canvas_obj.setFillColor(colors.Color(0.4, 0.4, 0.4))

    # Build device display string with inventory number
    device_display = device_name or "—"
    if inventory_number:
        device_display = f"{device_display} ({inventory_number})"

    # Header line at top (use placeholders for empty values). Long names are
    # cut so the three items never run into each other.
    y_pos = A4[1] - 1.2 * cm
    text_width = A4[0] - 4 * cm
    device_display = _fit_text(device_display, font, size, 6.5 * cm)
    hospital_width = (text_width - pdfmetrics.stringWidth(device_display, font, size)) / 2 - 4 * mm
    canvas_obj.drawString(2 * cm, y_pos, _fit_text(hospital_name or "—", font, size, hospital_width))
    canvas_obj.drawCentredString(A4[0] / 2, y_pos, device_display)
    canvas_obj.drawRightString(A4[0] - 2 * cm, y_pos, control_datetime)

    # Separator line
    canvas_obj.setStrokeColor(colors.Color(0.8, 0.8, 0.8))
    canvas_obj.line(2 * cm, y_pos - 3 * mm, A4[0] - 2 * cm, y_pos - 3 * mm)

    canvas_obj.restoreState()


class PDFReportGenerator:
    """Generates PDF reports for CT QC results."""

    def __init__(
        self,
        hospital_name: str = "[Nom de l'établissement]",
        hospital_location: str = "[Localisation de l'équipement]",
        device_name: str = "[Nom de l'équipement]",
        commissioning_date: str = "[Date de mise en service]",
        serial_number: str = "[Numéro de série]",
        inventory_number: str = "[Numéro d'inventaire]",
        reference_noise: Optional[float] = None,
        reference_nps_freq: Optional[float] = None,
        logo_path: Optional[str] = None,
        logo_scale: float = 1.0,
        notes: str = "",
        history: Optional[list[QCRun]] = None,
        phantom_brand: str = "",
        phantom_model: str = "",
        phantom_serial: str = "",
        clinical_protocol_origin: str = "",
        reconstruction_algorithm: str = "",
        dicom_folder: str = "",
        control_type: str = DEFAULT_CONTROL_TYPE,
        performed_by: str = "",
        validated_by: str = "",
    ):
        _ensure_reportlab()
        self.history = list(history or [])
        # Register of operations items (ANSM 3.2.2) entered on the installation
        self.phantom_brand = phantom_brand
        self.phantom_model = phantom_model
        self.phantom_serial = phantom_serial
        self.clinical_protocol_origin = clinical_protocol_origin
        self.reconstruction_algorithm = reconstruction_algorithm
        self.dicom_folder = dicom_folder
        self.hospital_name = hospital_name
        self.hospital_location = hospital_location
        self.device_name = device_name
        self.commissioning_date = commissioning_date
        self.serial_number = serial_number
        self.inventory_number = inventory_number
        self.reference_noise = reference_noise
        self.reference_nps_freq = reference_nps_freq
        self.logo_path = logo_path
        self.logo_scale = logo_scale
        self.notes = notes
        # Type of control (CONTROL_TYPES or free text) and who signs the report
        self.control_type = (control_type or "").strip()
        self.performed_by = (performed_by or "").strip()
        self.validated_by = (validated_by or "").strip()
        self.styles = getSampleStyleSheet()
        self._setup_styles()

    def _setup_styles(self):
        """Set up custom paragraph styles using Helvetica (clean sans-serif)."""
        # Use Helvetica family for a clean, professional look
        base_font = 'Helvetica'
        bold_font = 'Helvetica-Bold'

        self.styles.add(ParagraphStyle(
            name='ReportTitle',
            fontName=bold_font,
            fontSize=18,
            leading=22,
            alignment=TA_CENTER,
            spaceAfter=8,
            textColor=colors.Color(0.2, 0.2, 0.3),
        ))

        self.styles.add(ParagraphStyle(
            name='ReportSubtitle',
            fontName=base_font,
            fontSize=11,
            leading=14,
            alignment=TA_CENTER,
            spaceAfter=2,
            textColor=colors.Color(0.35, 0.35, 0.45),
        ))

        # Section titles stay with what follows them (never alone at a page bottom)
        self.styles.add(ParagraphStyle(
            name='SectionTitle',
            fontName=bold_font,
            fontSize=12,
            leading=15,
            spaceBefore=8,
            spaceAfter=5,
            textColor=colors.Color(0.2, 0.2, 0.4),
            borderPadding=0,
            keepWithNext=1,
        ))

        self.styles.add(ParagraphStyle(
            name='SubSection',
            fontName=bold_font,
            fontSize=10,
            leading=13,
            spaceBefore=6,
            spaceAfter=3,
            textColor=colors.Color(0.3, 0.3, 0.4),
            keepWithNext=1,
        ))

        self.styles.add(ParagraphStyle(
            name='InfoText',
            fontName=base_font,
            fontSize=9.5,
            leading=13,
        ))

        # Text of a table cell: wraps inside its column
        self.styles.add(ParagraphStyle(
            name='CellText',
            fontName=base_font,
            fontSize=9,
            leading=11.5,
        ))

        self.styles.add(ParagraphStyle(
            name='CellSmall',
            fontName=base_font,
            fontSize=8,
            leading=10,
        ))

        self.styles.add(ParagraphStyle(
            name='Caption',
            fontName=base_font,
            fontSize=8,
            leading=10,
            alignment=TA_CENTER,
            textColor=colors.Color(0.3, 0.3, 0.3),
        ))

        self.styles.add(ParagraphStyle(
            name='ListItem',
            parent=self.styles['InfoText'],
            leftIndent=18,
            bulletIndent=8,
        ))

        for name, status in (('ResultOK', OK), ('ResultNC', NC), ('ResultNCG', NCG),
                             # A test that was not judged: neither a pass nor a failure
                             ('ResultPending', PENDING)):
            self.styles.add(ParagraphStyle(
                name=name,
                fontName=bold_font,
                fontSize=9.5,
                leading=13,
                textColor=_status_color(status),
            ))
            # Same verdict inside a table
            self.styles.add(ParagraphStyle(
                name=name + 'Cell',
                parent=self.styles[name],
                fontSize=8,
                leading=10,
            ))

    def _para(self, markup: str, style: str = 'InfoText') -> Paragraph:
        """Paragraph of the report's own wording (markup allowed)."""
        return Paragraph(_fr(markup), self.styles[style])

    def _cell(self, text, style: str = 'CellText') -> Paragraph:
        """Free text (user entry, DICOM value) as a cell that wraps instead of overflowing."""
        return Paragraph(_fr(escape(str(text), quote=False)), self.styles[style])

    def _fields_table(self, rows: list[list], col_widths: list[float], splittable: bool = True) -> Table:
        """Table of "label : value" fields, one or several pairs per row.

        Labels (even columns) are bold; values given as text go through a
        Paragraph so that a long entry wraps in its column.
        """
        data = [[_fr(c) if i % 2 == 0 else (self._cell(c) if isinstance(c, str) else c)
                 for i, c in enumerate(row)] for row in rows]
        table = Table(data, colWidths=col_widths, splitByRow=1 if splittable else 0)
        style = [
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ]
        style += [('FONTNAME', (col, 0), (col, -1), 'Helvetica-Bold') for col in range(0, len(col_widths), 2)]
        table.setStyle(TableStyle(style))
        return table

    def _result_lines(self, status: str, pending_text: str = "") -> list:
        """"Résultat : …" line of a test, then the action the decision requires."""
        if status == PENDING:
            return [self._para(f"Résultat : {pending_text}", 'ResultPending')]
        text, style = _verdict(status, upper=True)
        lines = [self._para(f"Résultat : {text}", style)]
        action = _action_text(status)
        if action:
            lines.append(self._para(f"<i>→ {action}</i>", style))
        return lines

    def generate_report(
        self,
        output_path: str | Path,
        image: DicomImage,
        water_results: Optional[WaterPhantomResults] = None,
        nps_results: Optional[NPSResult] = None,
        artifact_result: Optional[ArtifactInspectionResult] = None,
        nps_image: Optional[DicomImage] = None,
        hu_slice_index: Optional[int] = None,
        nps_slice_range: Optional[tuple[int, int]] = None,
        total_slices: Optional[int] = None,
        control_date: str = "",
        control_date_manual: bool = False,
    ):
        """
        Generate a PDF report.

        Args:
            output_path: Path to save the PDF.
            image: DicomImage with metadata (used for HU ROI visualization).
            water_results: Water phantom analysis results.
            nps_results: NPS analysis results.
            artifact_result: Artifact inspection result.
            nps_image: DicomImage for NPS ROI visualization (middle of NPS range).
            hu_slice_index: 0-based index of ``image`` in the series (HU slice).
            nps_slice_range: 0-based (first, last) slices of the NPS analysis, inclusive.
            total_slices: Number of slices of the series.
            control_date: ISO date of the control when the images carry none
                (typed by the user); by default the date of the images.
            control_date_manual: True when ``control_date`` was typed by hand.
        """
        output_path = Path(output_path)

        # The control date is the day the phantom was scanned (DICOM study date),
        # which is also the date recorded in the history; the export time is
        # shown separately so both are traceable. Images without a date are
        # never dated from the day of the export.
        control_iso = control_date or dicom_date_to_iso(image.acquisition_date or image.study_date)
        control_date = iso_to_fr(control_iso) if control_iso else UNKNOWN_DATE
        self._control_date_text = control_date + (
            " (saisie manuellement : absente des images DICOM)" if control_date_manual and control_iso
            else "")
        export_datetime = datetime.now().strftime('%d/%m/%Y %H:%M')

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            # The frame pads its content by 6 pt on each side: text and 17 cm
            # tables then both start 2 cm from the page edge
            rightMargin=2 * cm - 6,
            leftMargin=2 * cm - 6,
            topMargin=1.8 * cm,  # Room for the header of pages 2+
            bottomMargin=1.6 * cm,  # Room for the footer
            title=f"Contrôle de qualité interne {self._dated(control_date)}",
            author=self.performed_by,
            creator=f"CQ TDM {__version__}",
        )

        story = []

        # Logo (if provided)
        if self.logo_path:
            try:
                # Text width = A4 width (21cm) - margins (2cm each side) = 17cm
                # Scale is percentage of text width (100% = full width)
                text_width = 17 * cm

                # Load image to get aspect ratio
                from PIL import Image as PILImage
                with PILImage.open(self.logo_path) as pil_img:
                    img_width, img_height = pil_img.size
                aspect = img_width / img_height

                # Apply scale to text width, height follows aspect ratio
                final_width = text_width * self.logo_scale
                final_height = final_width / aspect
                # A tall (portrait) logo must still fit on the first page with
                # the title and summary; cap its height and shrink the width.
                max_height = 6 * cm
                if final_height > max_height:
                    final_height = max_height
                    final_width = final_height * aspect

                logo_img = Image(self.logo_path, width=final_width, height=final_height)
                logo_img.hAlign = 'CENTER'
                story.append(logo_img)
                story.append(Spacer(1, 10))
            except Exception:
                logger.exception("Logo %s could not be added to the report", self.logo_path)

        # Title
        story.append(Paragraph(
            "Rapport de contrôle de qualité interne",
            self.styles['ReportTitle']
        ))
        story.append(Paragraph(
            "Contrôle de qualité des tomodensitomètres — décision ANSM du 18/12/2025",
            self.styles['ReportSubtitle']
        ))
        control_type = f" {escape(self.control_type, quote=False)}" if self.control_type else ""
        story.append(Paragraph(
            f"<b>Contrôle de qualité interne{control_type} {self._dated(control_date)}</b>",
            self.styles['ReportSubtitle']
        ))
        edited = f"Rapport édité le {export_datetime}"
        if self.performed_by:
            edited += f" — contrôle réalisé par {escape(self.performed_by, quote=False)}"
        story.append(Paragraph(edited, self.styles['ReportSubtitle']))
        story.append(Spacer(1, 10))

        # Summary badge at the top
        overall, overall_status, overall_color, overall_action = self._overall_verdict(
            water_results, nps_results, artifact_result
        )
        symbol = _VERDICT[overall][0] + " " if overall in _VERDICT else ""
        story.append(_create_status_badge(symbol + overall_status, overall_color))
        if overall_action:
            story.append(Spacer(1, 5))
            story.append(Paragraph(
                _fr(f"<b>{overall_action}</b>"),
                ParagraphStyle(
                    'ActionText',
                    parent=self.styles['InfoText'],
                    alignment=TA_CENTER,
                    textColor=overall_color,
                    fontSize=10,
                )
            ))
        undetected = self._phantom_warning(water_results, nps_results)
        if undetected:
            story.append(Spacer(1, 5))
            story.append(Paragraph(
                _fr(f"<b>Attention :</b> {undetected}"),
                ParagraphStyle('PhantomWarning', parent=self.styles['InfoText'],
                               alignment=TA_CENTER, textColor=_status_color(NC))))
        story.append(Spacer(1, 6))

        # Slices the measurements were made on
        hu_slices = _hu_slice_text(image, hu_slice_index, total_slices)
        nps_slices = _nps_slice_text(nps_results, nps_slice_range) if nps_results else ""

        # Short blocks are kept whole; longer sections may run over a page, their
        # title staying with what follows it (keepWithNext of the title styles)

        # Summary section at the beginning
        story.append(KeepTogether(self._build_summary_section(water_results, nps_results, artifact_result)))

        # Equipment info section
        story.extend(self._build_equipment_section(image))

        # Acquisition parameters section
        story.extend(self._build_acquisition_section(image, hu_slices, nps_slices))

        # ROI visualization (if any results available)
        if water_results or nps_results:
            story.extend(self._build_roi_visualization_section(
                image, water_results, nps_results, nps_image, hu_slices, nps_slices
            ))

        # Water phantom results (Nombre CT, Uniformité, Bruit)
        if water_results:
            story.append(KeepTogether(self._build_ct_number_section(water_results)))
            story.append(KeepTogether(self._build_uniformity_section(water_results)))
        if water_results or nps_results:
            story.append(KeepTogether(self._build_noise_section(water_results, nps_results)))

        # NPS results with the radial curve, then the warnings
        if nps_results:
            story.append(KeepTogether(self._build_nps_values_and_plot(nps_results, nps_slices)))
            story.extend(self._build_nps_warnings(nps_results))

        # Artifact inspection results
        story.append(KeepTogether(self._build_artifact_section(image, artifact_result, hu_slices)))

        # History of previous controls and trends (if any)
        if self.history:
            story.extend(self._build_history_section())

        # Notes section (if any)
        if self.notes and self.notes.strip():
            story.extend(self._build_notes_section())

        # Names and signatures
        story.append(KeepTogether(self._build_validation_section()))

        def on_later_pages(canvas_obj, doc):
            """Pages 2+: header (the footer of every page is drawn by the canvas)."""
            _draw_header(canvas_obj, self.hospital_name, self.device_name, self.inventory_number, control_date)

        footer = f"CQ TDM {__version__} · {self._control_phrase(control_date)} · édité le {export_datetime}"
        doc.build(story, onLaterPages=on_later_pages, canvasmaker=_numbered_canvas(footer))

    @staticmethod
    def _dated(control_date: str) -> str:
        """"du JJ/MM/AAAA" after the name of the control, or "(date inconnue)"."""
        return f"({UNKNOWN_DATE})" if control_date == UNKNOWN_DATE else f"du {control_date}"

    @staticmethod
    def _control_phrase(control_date: str) -> str:
        """"contrôle du JJ/MM/AAAA", or "date du contrôle inconnue"."""
        if control_date == UNKNOWN_DATE:
            return "date du contrôle inconnue"
        return f"contrôle du {control_date}"

    @staticmethod
    def _phantom_warning(
        water_results: Optional[WaterPhantomResults], nps_results: Optional[NPSResult]
    ) -> str:
        """Sentence printed when the ROIs were placed without finding the phantom wall, else ""."""
        where = []
        if water_results is not None and not getattr(water_results, "phantom_detected", True):
            where.append("la coupe UH")
        if nps_results is not None and not getattr(nps_results, "phantom_detected", True):
            where.append("la coupe médiane de la plage SPB")
        if not where:
            return ""
        return ("la paroi du fantôme n'a pas été détectée sur " + " ni sur ".join(where)
                + ". Les ROI ont été placées par défaut : les mesures ne sont valables "
                "que si elles sont dans l'eau (voir « Visualisation des ROI »).")

    def _statuses(
        self,
        water_results: Optional[WaterPhantomResults],
        nps_results: Optional[NPSResult],
        artifact_result: Optional[ArtifactInspectionResult],
    ) -> dict[str, str]:
        """Status of each test plus ``overall``, by the shared criteria (qc_history)."""
        return evaluate_measurements(
            water_results.water_ct_number if water_results else None,
            water_results.uniformity if water_results else None,
            nps_results.noise if nps_results else None,
            nps_results.mean_frequency if nps_results else None,
            artifact_result.artifacts_present if artifact_result is not None else None,
            self.reference_noise,
            self.reference_nps_freq,
        )

    def _overall_verdict(
        self,
        water_results: Optional[WaterPhantomResults],
        nps_results: Optional[NPSResult],
        artifact_result: Optional[ArtifactInspectionResult],
    ) -> tuple[str, str, colors.Color, str]:
        """Overall status, its badge text, its color and the required action.

        Same rule as the history (qc_history): "CONFORME" requires every test to
        have been judged, so a control without artifact inspection or without
        reference values is reported as incomplete, not as conforming.
        """
        statuses = self._statuses(water_results, nps_results, artifact_result)
        overall = statuses["overall"]
        if overall == NC_OR_NCG:
            return overall, "NC OU NCG", _status_color(overall), NC_OR_NCG_DETAIL
        if overall in (OK, NC, NCG):
            return overall, _VERDICT[overall][1].upper(), _status_color(overall), _action_text(overall)
        reasons = pending_reasons(statuses, nps_measured=nps_results is not None,
                                  noise_measured=nps_results is not None)
        return (overall, "CONTRÔLE INCOMPLET", colors.Color(0.45, 0.45, 0.45),
                "Conformité non établie : " + " ; ".join(reasons))

    def _compute_overall_status(
        self,
        water_results: Optional[WaterPhantomResults],
        nps_results: Optional[NPSResult],
        artifact_result: Optional[ArtifactInspectionResult],
    ) -> tuple[str, colors.Color, str]:
        """Compute overall status, color, and required action."""
        return self._overall_verdict(water_results, nps_results, artifact_result)[1:]

    def _build_equipment_section(self, image: DicomImage) -> list:
        """Build equipment information section."""
        elements = []

        elements.append(Paragraph("Installation", self.styles['SectionTitle']))

        # Helper to display empty fields as "Non renseigné"
        def val(s: str) -> str:
            return s if s else "Non renseigné"

        widths = [5.2 * cm, 11.8 * cm]
        elements.append(self._fields_table([
            ["Établissement :", val(self.hospital_name)],
            ["Localisation :", val(self.hospital_location)],
        ], widths))
        # Short identifiers, two per row
        elements.append(self._fields_table([
            ["Fabricant :", image.manufacturer or "—", "Modèle :", image.model_name or "—"],
            ["Numéro de série :", val(self.serial_number), "Numéro d'inventaire :", val(self.inventory_number)],
            ["Date de mise en service :", val(self.commissioning_date), "", ""],
        ], [5.2 * cm, 3.9 * cm, 3.7 * cm, 4.2 * cm]))
        # Register of operations items (ANSM 3.2.2)
        phantom = " ".join(p for p in (self.phantom_brand, self.phantom_model) if p)
        if self.phantom_serial:
            phantom = f"{phantom} (n° série {self.phantom_serial})" if phantom else f"n° série {self.phantom_serial}"
        elements.append(self._fields_table([
            ["Fantôme de CQ :", val(phantom)],
            ["Protocole clinique d'origine :", val(self.clinical_protocol_origin)],
            ["Algorithme de reconstruction :", val(self.reconstruction_algorithm)],
        ], widths))
        elements.append(Spacer(1, 4))

        return elements

    def _build_acquisition_section(self, image: DicomImage, hu_slices: str = "", nps_slices: str = "") -> list:
        """Build acquisition parameters section.

        ``hu_slices`` and ``nps_slices`` describe the slices the measurements
        were made on (_hu_slice_text, _nps_slice_text).
        """
        elements = []

        elements.append(Paragraph("Paramètres d'acquisition", self.styles['SectionTitle']))

        # The register must list, for the QC protocol: mAs, kV, slice thickness,
        # focal spot, collimation, pitch, reconstruction algorithm (decision, point 5)
        ctdi = f"{format_fr(image.ctdi_vol, 2)} mGy" if image.ctdi_vol > 0 else "—"
        if image.ctdi_vol > 0 and image.ctdi_phantom:
            ctdi += f" (fantôme {image.ctdi_phantom})"
        fields = [
            ("Date d'acquisition :", getattr(self, "_control_date_text", "") or UNKNOWN_DATE),
            ("Protocole :", image.series_description or "—"),
            ("Tension (kV) :", f"{image.kvp:.0f}" if image.kvp else "—"),
            ("Courant (mA) :", f"{image.tube_current:.0f}" if image.tube_current else "—"),
            ("Charge (mAs) :", format_fr(image.mas, 0) if image.mas > 0 else "—"),
            ("Temps de rotation :", f"{format_fr(image.rotation_time, 2)} s" if image.rotation_time > 0 else "—"),
            ("Mode :", image.acquisition_mode or "—"),
            ("Collimation :", image.collimation or "—"),
            ("Foyer :", _focal_spots_text(image.focal_spots)),
            ("Filtre / algorithme :", image.convolution_kernel or "—"),
            ("Épaisseur de coupe :", f"{format_fr(image.slice_thickness, 1)} mm" if image.slice_thickness else "—"),
            ("Taille de pixel :", f"{format_fr(image.pixel_size_mm, 3)} mm"),
            ("Matrice :", f"{image.columns} × {image.rows}" if image.rows and image.columns else "—"),
            ("Champ de vue :", f"{image.fov:.0f} mm" if image.fov else "—"),
            ("IDSV affiché :", ctdi),
        ]
        # Two fields per row
        fields.append(("", ""))
        rows = [[*fields[i], *fields[i + 1]] for i in range(0, len(fields) - 1, 2)]
        elements.append(self._fields_table(rows, [3.4 * cm, 5.1 * cm, 3.4 * cm, 5.1 * cm]))

        # Slices used, and the images the results refer to: archived in native
        # DICOM format (register), so identify the series and where it is kept
        small = self.styles['CellSmall']
        archive = [
            ["Coupe UH :", (hu_slices + " — nombre CT, uniformité") if hu_slices else "—"],
            ["Coupes SPB :", (nps_slices + " — bruit, SPB") if nps_slices else "SPB non calculé"],
            ["Série DICOM (UID) :", Paragraph(escape(image.series_instance_uid, quote=False) or "—", small)],
            ["Dossier des images :", Paragraph(escape(self.dicom_folder, quote=False) or "Non renseigné", small)],
        ]
        # Kept whole: a single row carried over to the next page reads badly
        elements.append(self._fields_table(archive, [3.4 * cm, 13.6 * cm], splittable=False))
        elements.append(Spacer(1, 4))

        return elements

    def _build_roi_visualization_section(
        self,
        image: DicomImage,
        water_results: Optional[WaterPhantomResults] = None,
        nps_results: Optional[NPSResult] = None,
        nps_image: Optional[DicomImage] = None,
        hu_slices: str = "",
        nps_slices: str = "",
    ) -> list:
        """Build ROI visualization section with side-by-side images."""
        elements = []

        elements.append(Paragraph("Visualisation des ROI", self.styles['SectionTitle']))
        undetected = self._phantom_warning(water_results, nps_results)
        if undetected:
            elements.append(Paragraph(
                _fr(f"<b>Attention :</b> {undetected}"),
                ParagraphStyle('PhantomWarningLeft', parent=self.styles['InfoText'],
                               textColor=_status_color(NC))))

        # Build table with images side by side, each over its caption
        images_row = []
        captions_row = []
        size = 6.2

        if water_results:
            try:
                hu_buffer = _generate_hu_roi_image(image, water_results, size_cm=size)
                hu_img = Image(hu_buffer, width=size * cm, height=size * cm)
                images_row.append(hu_img)
            except Exception:
                logger.exception("HU ROI image could not be drawn")
                images_row.append(Paragraph("Image UH non disponible", self.styles['InfoText']))
            captions_row.append(self._para(
                "ROI du nombre CT et de l'uniformité" + (f" — coupe {hu_slices}" if hu_slices else ""),
                'Caption'))

        if nps_results and nps_results.roi_config.rois:
            try:
                # Use nps_image if provided (middle of NPS range), otherwise fallback to main image
                nps_display_image = nps_image if nps_image is not None else image
                nps_buffer = _generate_nps_roi_image(nps_display_image, nps_results, size_cm=size)
                nps_img = Image(nps_buffer, width=size * cm, height=size * cm)
                images_row.append(nps_img)
            except Exception:
                logger.exception("SPB ROI image could not be drawn")
                images_row.append(Paragraph("Image SPB non disponible", self.styles['InfoText']))
            captions_row.append(self._para(
                "ROI du SPB et du bruit" + (f" — coupes {nps_slices}" if nps_slices else ""), 'Caption'))

        if images_row:
            # Create table with images
            img_table = Table([images_row, captions_row], colWidths=[8.5 * cm] * len(images_row))
            img_table.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
                ('VALIGN', (0, 1), (-1, 1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]))
            elements.append(img_table)

        elements.extend(self._build_roi_positions_table(image, water_results, nps_results))
        elements.append(Spacer(1, 6))

        return elements

    def _build_roi_positions_table(
        self,
        image: DicomImage,
        water_results: Optional[WaterPhantomResults],
        nps_results: Optional[NPSResult],
    ) -> list:
        """Position and size of every ROI in pixels and mm (register item for the SPB)."""
        px = image.pixel_size_mm
        rows = [["ROI", "Centre X (px)", "Centre Y (px)", "Taille (px)", "Taille (mm)"]]
        if water_results:
            measurements = (water_results.central, water_results.top, water_results.right,
                            water_results.bottom, water_results.left)
            # Same labels as on the image
            for m, label, name in zip(measurements, HU_ROI_LABELS, HU_ROI_NAMES):
                rows.append([f"UH {label} ({name})", str(m.center_col), str(m.center_row),
                             f"Ø {2 * m.radius}", f"Ø {format_fr(2 * m.radius * px, 1)}"])
        if nps_results and nps_results.roi_config.rois:
            skipped = set(nps_results.skipped_rois)
            for i, r in enumerate(nps_results.roi_config.rois):
                name = NPS_ROI_NAMES[i] if i < len(NPS_ROI_NAMES) else str(i + 1)
                side_mm = format_fr(r.side_square * px, 1)
                rows.append([f"SPB {i + 1} ({name})" + (" — hors image, ignorée" if i in skipped else ""),
                             str(r.x), str(r.y),
                             f"{r.side_square} × {r.side_square}", f"{side_mm} × {side_mm}"])
        if len(rows) == 1:
            return []

        geometry = water_results.geometry if water_results is not None else None
        if geometry is None and nps_results is not None:
            geometry = nps_results.geometry
        if geometry is not None and geometry.is_frozen:
            origin = (f"Tailles et positions des ROI figées depuis le contrôle de référence du "
                      f"{iso_to_fr(geometry.frozen_date)} et réutilisées à l'identique "
                      f"(seul le centre du fantôme est redétecté).")
        else:
            origin = "Tailles et positions des ROI calculées sur cette série."
        elements = [Spacer(1, 3), self._para(
            origin + " Coordonnées en pixels, origine en haut à gauche de l'image "
            "(X vers la droite, Y vers le bas).", 'CellSmall'), Spacer(1, 3)]
        table = Table(rows, colWidths=[5.0 * cm, 3.0 * cm, 3.0 * cm, 3.0 * cm, 3.0 * cm], repeatRows=1)
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
            ('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.grey),
            ('LINEBELOW', (0, 1), (-1, -1), 0.25, colors.lightgrey),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))
        elements.append(table)
        return elements

    def _build_artifact_section(
        self,
        image: DicomImage,
        artifact_result: Optional[ArtifactInspectionResult],
        hu_slices: str = "",
    ) -> list:
        """Build artifact inspection results section: image next to the verdict."""
        elements = []

        elements.append(Paragraph("Inspection visuelle des artéfacts", self.styles['SectionTitle']))

        details = [self._para("Fenêtre : centre 0 UH, largeur 80 UH")]
        if hu_slices:
            details.append(self._para(f"Coupe affichée : {hu_slices}"))
        details.append(Spacer(1, 6))

        status = artifacts_status(artifact_result.artifacts_present if artifact_result is not None else None)
        description = None
        if artifact_result is not None:
            observation = "Présence d'artéfacts" if artifact_result.artifacts_present else "Absence d'artéfacts"
            details.append(self._para(f"<b>Observation :</b> {observation}"))
            if artifact_result.artifacts_present and artifact_result.description:
                # Free text: escaped, wraps in its column
                description = self._para(
                    f"<b>Description :</b> {escape(artifact_result.description, quote=False)}")
                # A very long one goes under the image, where it can run over
                # a page (a table row cannot)
                if len(artifact_result.description) <= 600:
                    details.append(description)
                    description = None
            details.append(Spacer(1, 4))
        details.extend(self._result_lines(
            status, "TEST NON RÉALISÉ — inspection visuelle des artéfacts à effectuer"))

        # Add artifact inspection image
        size = 9.0
        try:
            artifact_buffer = _generate_artifact_image(image, size_cm=size)
            artifact_img = Image(artifact_buffer, width=size * cm, height=size * cm)
            table = Table([[artifact_img, details]], colWidths=[(size + 0.5) * cm, (16.5 - size) * cm])
            table.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
            ]))
            elements.append(table)
        except Exception:
            logger.exception("Artifact image could not be drawn")
            elements.extend(details)  # Skip image if generation fails
        if description is not None:
            elements.append(description)

        elements.append(Spacer(1, 6))
        return elements

    def _build_history_section(self) -> list:
        """Table of previous controls and trend charts for the stability tests."""
        elements = []
        elements.append(Paragraph("Historique des contrôles", self.styles['SectionTitle']))

        runs = sorted(self.history, key=lambda r: (r.run_date, r.recorded_at), reverse=True)
        shown = runs[:10]
        elements.append(self._para(
            f"{len(runs)} contrôle{'s' if len(runs) > 1 else ''} enregistré{'s' if len(runs) > 1 else ''} "
            f"pour cette installation" + (f" ({len(shown)} plus récents affichés)" if len(runs) > len(shown) else "")
            + ". Les statuts sont ceux établis avec les valeurs de référence en vigueur à la date de chaque contrôle."))
        elements.append(Spacer(1, 4))

        header = ["Date", "kV / mAs", "CT eau", "Uniformité", "Bruit σ", "f SPB", "Artéfacts", "Statut"]
        data = [header]
        cell_status = []  # (row, col, status) for colouring
        for i, run in enumerate(shown, start=1):
            st = evaluate_run(run)
            overall = STATUS_SHORT[st["overall"]]
            if st["overall"] in _VERDICT:
                overall = f"{_VERDICT[st['overall']][0]} {overall}"
            data.append([
                run.date_fr(),
                f"{format_fr(run.kvp, 0)} / {format_fr(run.mas, 0)}" if run.kvp else "—",
                f"{format_fr(run.water_ct, 1, sign=True)} UH",
                f"{format_fr(run.uniformity, 1)} UH",
                "—" if run.noise is None else f"{format_fr(run.noise, 2)} UH",
                "—" if run.nps_freq is None else f"{format_fr(run.nps_freq, 3)}",
                {None: "—", False: "Absents", True: "Présents"}[run.artifacts_present],
                overall,
            ])
            for col, key in ((2, "water_ct"), (3, "uniformity"), (4, "noise"), (5, "nps_freq"),
                             (6, "artifacts"), (7, "overall")):
                cell_status.append((i, col, st[key]))

        table = Table(data, colWidths=[2.2 * cm, 2.0 * cm, 2.1 * cm, 2.1 * cm, 2.1 * cm, 1.8 * cm, 2.1 * cm, 2.6 * cm],
                      repeatRows=1)
        style = [
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
            ('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.grey),
            ('LINEBELOW', (0, 1), (-1, -1), 0.25, colors.lightgrey),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
        ]
        for row, col, status in cell_status:
            if status == OK and col == 7:
                style.append(('TEXTCOLOR', (col, row), (col, row), _status_color(OK)))
            elif status in (NC, NCG, NC_OR_NCG):
                style.append(('TEXTCOLOR', (col, row), (col, row), _status_color(status)))
                # Bold as well: a non-conforming value must stand out without colour
                style.append(('FONTNAME', (col, row), (col, row), 'Helvetica-Bold'))
            elif status in (PENDING, INCOMPLETE):
                style.append(('TEXTCOLOR', (col, row), (col, row), colors.Color(0.4, 0.4, 0.4)))
        table.setStyle(TableStyle(style))
        elements.append(table)
        elements.append(Spacer(1, 6))

        # Corrective actions (register: date of the actions taken to restore conformity)
        actions = [r for r in runs if r.corrective_action_date or r.corrective_action]
        if actions:
            elements.append(Paragraph("Actions correctives", self.styles['SubSection']))
            for r in actions:
                text = (f"Contrôle du {r.date_fr()} : action corrective le "
                        f"{iso_to_fr(r.corrective_action_date) or '(date non renseignée)'}")
                if r.corrective_action:
                    text += f" — {r.corrective_action}"
                elements.append(self._cell(text, 'InfoText'))
            elements.append(Spacer(1, 6))

        # Trend charts for the two stability tests, one under the other at full
        # width so the dates stay legible
        charts = []
        palette = dict(LIGHT_PALETTE, nc='#b45309')
        for metric, title in (("noise", "Bruit σ (UH)"), ("nps_freq", "Fréquence moyenne du SPB (cycles/mm)")):
            if not any(getattr(r, metric) is not None for r in runs):
                continue
            chart = render_trend_chart(
                runs, metric, self.reference_noise, self.reference_nps_freq,
                palette=palette, width_px=1260, height_px=295, dpi=200, title=title, ylabel="")
            charts.append([Image(BytesIO(chart.png), width=16 * cm, height=16 * cm * 295 / 1260)])
        if charts:
            # The title stays with the first chart and the legend with the last one
            charts[0].insert(0, Paragraph("Tendances", self.styles['SubSection']))
            charts[-1].append(self._para(
                "Bande verte : tolérance ANSM autour de la référence actuelle ; points orange : "
                "non conformes ; points rouges : non-conformité grave.", 'Caption'))
            elements.extend(KeepTogether(block) for block in charts)
        elements.append(Spacer(1, 6))
        return elements

    def _build_notes_section(self) -> list:
        """Build notes section with simplified markdown rendering."""
        elements = []

        elements.append(Paragraph("Observations du contrôle", self.styles['SectionTitle']))

        def parse_inline_formatting(text: str) -> str:
            """Convert **bold** and *italic* to HTML tags (the text itself is escaped)."""
            text = escape(text, quote=False)
            # Bold: **text**
            text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
            # Italic: *text* (but not inside bold)
            text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<i>\1</i>', text)
            return text

        # Parse simplified markdown and convert to paragraphs
        for line in self.notes.split('\n'):
            stripped = line.strip()

            if not stripped:
                elements.append(Spacer(1, 4))
                continue

            # Check for heading (## Title)
            if stripped.startswith('## '):
                heading_text = parse_inline_formatting(stripped[3:])
                elements.append(Paragraph(heading_text, self.styles['SubSection']))
                continue

            # Check for list item (- item): a Paragraph too, so the inline
            # formatting is rendered and a long item wraps
            if stripped.startswith('- '):
                item_text = parse_inline_formatting(stripped[2:])
                elements.append(Paragraph(item_text, self.styles['ListItem'], bulletText='•'))
                continue

            # Regular paragraph
            para_text = parse_inline_formatting(stripped)
            elements.append(Paragraph(para_text, self.styles['InfoText']))

        elements.append(Spacer(1, 6))
        return elements

    def _build_validation_section(self) -> list:
        """Who performed and who validated the control, with room to date and sign."""
        elements = [Paragraph("Validation", self.styles['SectionTitle'])]

        def box(label: str, value: str = "") -> list:
            # An empty name leaves the box blank, to be filled in by hand
            return [self._para(label, 'CellSmall'), self._cell(value, 'InfoText')]

        def signature() -> list:
            # Room to sign: sets the height of the row
            return [self._para("Signature :", 'CellSmall'), Spacer(1, 0.75 * cm)]

        data = [
            [box("Réalisé par :", self.performed_by), box("Date :"), signature()],
            [box("Validé par (physicien médical) :", self.validated_by), box("Date :"), signature()],
        ]
        table = Table(data, colWidths=[7.5 * cm, 3.5 * cm, 6.0 * cm])
        table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.Color(0.6, 0.6, 0.6)),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(table)
        return elements

    def _build_ct_number_section(self, results: WaterPhantomResults) -> list:
        """Build CT number section."""
        elements = []

        elements.append(Paragraph("Nombre CT de l'eau", self.styles['SectionTitle']))

        data = [
            ["Valeur mesurée :", f"{format_fr(results.water_ct_number, 1, sign=True)} UH (moyenne de la ROI centrale)"],
            ["Critère d'acceptabilité :", "-7 UH ≤ nombre CT ≤ +7 UH ; non-conformité grave au-delà de ±25 UH"],
        ]
        elements.append(self._fields_table(data, [5.2 * cm, 11.8 * cm]))
        elements.append(Spacer(1, 3))
        elements.extend(self._result_lines(water_ct_status(results.water_ct_number)))
        elements.append(Spacer(1, 4))

        return elements

    def _build_uniformity_section(self, results: WaterPhantomResults) -> list:
        """Build uniformity section."""
        elements = []

        elements.append(Paragraph("Uniformité", self.styles['SectionTitle']))

        # Individual peripheral deviations table
        central_mean = results.central.mean_hu
        peripheral = (results.top, results.right, results.bottom, results.left)

        data = [["ROI", "Moyenne (UH)", "Écart par rapport au centre (UH)"]]
        for m, label, name in zip(peripheral, HU_ROI_LABELS[1:], HU_ROI_NAMES[1:]):
            data.append([f"{label} ({name})", format_fr(m.mean_hu, 1, sign=True),
                         format_fr(m.mean_hu - central_mean, 1, sign=True)])

        table = Table(data, colWidths=[4 * cm, 4 * cm, 6 * cm], hAlign='LEFT')
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
            ('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.grey),
            ('LINEBELOW', (0, 1), (-1, -1), 0.25, colors.lightgrey),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 4))

        summary_data = [
            ["Écart maximal centre-périphérie :", f"{format_fr(results.uniformity, 1)} UH"],
            ["Critère d'acceptabilité :", "écart ≤ 7 UH en valeur absolue"],
        ]
        elements.append(self._fields_table(summary_data, [5.2 * cm, 11.8 * cm]))
        elements.append(Spacer(1, 3))
        elements.extend(self._result_lines(uniformity_status(results.uniformity)))
        elements.append(Spacer(1, 4))

        return elements

    def _build_noise_section(
        self,
        water_results: Optional[WaterPhantomResults],
        nps_results: Optional[NPSResult],
    ) -> list:
        """Build noise section.

        The noise of the control is the mean of the standard deviations of the
        SPB ROIs over the analysed slices (decision, point 9.1.7.2); the
        standard deviation of the central ROI is printed for information.
        """
        elements = []

        elements.append(Paragraph("Bruit", self.styles['SectionTitle']))

        central_row = None
        if water_results:
            central_row = ["Écart-type ROI centrale :",
                           f"{format_fr(water_results.central.std_hu, 2)} UH (coupe UH, pour information)"]

        if nps_results is None:
            data = [["Bruit :", "non mesuré (SPB non calculé)"]]
            if central_row:
                data.append(central_row)
            elements.append(self._fields_table(data, [5.2 * cm, 11.8 * cm]))
            elements.append(Spacer(1, 3))
            elements.extend(self._result_lines(PENDING, "TEST NON RÉALISÉ"))
            elements.append(Spacer(1, 4))
            return elements

        noise = nps_results.noise
        data = [
            ["Bruit :", f"{format_fr(noise, 2)} UH"],
            ["Mesure :", f"moyenne des écarts-types de {nps_results.noise_roi_count} ROI du SPB "
                         f"({nps_results.num_slices} coupe{'s' if nps_results.num_slices > 1 else ''})"],
        ]

        # Add stability comparison if reference value is available
        if self.reference_noise is not None:
            deviation = noise - self.reference_noise
            # ANSM criterion: MIN(-0.2, -0.1*B_ref) ≤ (B_i - B_ref) ≤ MAX(0.2, 0.1*B_ref)
            lower_bound, upper_bound = noise_bounds(self.reference_noise)

            data.append(["Valeur de référence :", f"{format_fr(self.reference_noise, 2)} UH"])
            data.append(["Écart à la référence :", f"{format_fr(deviation, 2, sign=True)} UH"])
            data.append(["Critère d'acceptabilité :",
                         f"{format_fr(lower_bound, 2, sign=True)} UH ≤ écart ≤ {format_fr(upper_bound, 2, sign=True)} UH"])

        if central_row:
            data.append(central_row)

        elements.append(self._fields_table(data, [5.2 * cm, 11.8 * cm]))
        elements.append(Spacer(1, 3))
        elements.extend(self._result_lines(noise_status(noise, self.reference_noise),
                                           "NON ÉVALUÉ — valeur de référence absente"))
        elements.append(Spacer(1, 4))

        return elements

    def _build_nps_results_section(self, results: NPSResult, nps_slices: str = "") -> list:
        """Build NPS results section."""
        return self._build_nps_values_and_plot(results, nps_slices) + self._build_nps_warnings(results)

    def _build_nps_values_and_plot(self, results: NPSResult, nps_slices: str = "") -> list:
        """SPB values and verdict, next to the radial curve."""
        elements = []

        elements.append(Paragraph("Spectre de puissance du bruit (SPB)", self.styles['SectionTitle']))

        measured = len(results.roi_config.rois) - len(results.skipped_rois)
        data = [
            ["Coupes analysées :", nps_slices or str(results.num_slices)],
            ["ROI mesurées :", f"{measured} / {len(results.roi_config.rois)} "
                               f"({results.roi_size} × {results.roi_size} px)"],
            ["Fréquence moyenne :", f"{format_fr(results.mean_frequency, 3)} cycles/mm"],
        ]

        # Add stability comparison if reference value is available
        if self.reference_nps_freq is not None and self.reference_nps_freq > 0:
            deviation_pct = ((results.mean_frequency - self.reference_nps_freq) / self.reference_nps_freq) * 100
            lower_bound, upper_bound = nps_bounds(self.reference_nps_freq)

            data.append(["Valeur de référence :", f"{format_fr(self.reference_nps_freq, 3)} cycles/mm"])
            data.append(["Écart à la référence :", f"{format_fr(deviation_pct, 1, sign=True)} %"])
            data.append(["Critère d'acceptabilité :", self._para(
                f"-10 % ≤ écart ≤ +10 %<br/>soit {format_fr(lower_bound, 3)} "
                f"à {format_fr(upper_bound, 3)} cycles/mm", 'CellText')])

        values = [self._fields_table(data, [3.8 * cm, 4.4 * cm]), Spacer(1, 3)]
        values.extend(self._result_lines(nps_status(results.mean_frequency, self.reference_nps_freq),
                                         "NON ÉVALUÉ — valeur de référence absente"))

        # Add NPS plot
        try:
            nps_plot_buffer = _generate_nps_plot(results, 8.7, 5.8)
            nps_plot_img = Image(nps_plot_buffer, width=8.7 * cm, height=5.8 * cm)
            table = Table([[values, nps_plot_img]], colWidths=[8.3 * cm, 8.7 * cm])
            table.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
            ]))
            elements.append(table)
        except Exception:
            logger.exception("SPB plot could not be drawn")
            elements.extend(values)  # Skip plot if generation fails
        elements.append(Spacer(1, 4))

        return elements

    def _build_nps_warnings(self, results: NPSResult) -> list:
        """Warnings of the SPB analysis: too few slices, non-uniform ROI."""
        elements = []

        # Warning for insufficient slices (ANSM requires 10 slices)
        if results.num_slices < 10:
            warning_text = (
                f"<b>Attention :</b> L'ANSM recommande d'analyser 10 coupes centrées sur la coupe centrale. "
                f"Seulement {results.num_slices} coupe(s) utilisée(s)."
            )
            elements.append(self._para(warning_text, 'ResultNC'))
            elements.append(Spacer(1, 4))

        # ROI uniformity warnings (if any)
        if results.roi_warnings:
            elements.append(Paragraph("Avertissements", self.styles['SubSection']))

            warning_data = [["Coupe", "ROI", "Description"]]
            for warning in results.roi_warnings:
                message = warning.message.split(": ", 1)[-1] if ": " in warning.message else warning.message
                warning_data.append([
                    str(warning.slice_index + 1),
                    str(warning.roi_index + 1),
                    self._cell(message, 'CellSmall'),
                ])

            warning_table = Table(warning_data, colWidths=[2 * cm, 2 * cm, 13 * cm], repeatRows=1)
            warning_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('BACKGROUND', (0, 0), (-1, 0), colors.Color(1, 0.92, 0.85)),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.Color(0.75, 0.75, 0.75)),
                ('ALIGN', (0, 0), (1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
            ]))

            elements.append(warning_table)
            elements.append(Spacer(1, 4))

        return elements

    def _build_summary_section(
        self,
        water_results: Optional[WaterPhantomResults],
        nps_results: Optional[NPSResult],
        artifact_result: Optional[ArtifactInspectionResult] = None,
    ) -> list:
        """Build the summary table: one row per test with its reference, criterion and verdict."""
        elements = []

        elements.append(Paragraph("Résumé des tests", self.styles['SectionTitle']))

        statuses = self._statuses(water_results, nps_results, artifact_result)
        # (test, measured value, reference or expected value, criterion, status, wording if pending)
        rows = []

        rows.append((
            "Nombre CT de l'eau",
            f"{format_fr(water_results.water_ct_number, 1, sign=True)} UH" if water_results else "non mesuré",
            "0 UH",
            "-7 UH ≤ nombre CT ≤ +7 UH ; non-conformité grave au-delà de ±25 UH",
            statuses["water_ct"], NOT_DONE,
        ))
        rows.append((
            "Uniformité",
            f"{format_fr(water_results.uniformity, 1)} UH" if water_results else "non mesurée",
            "—",
            "écart maximal centre-périphérie ≤ 7 UH",
            statuses["uniformity"], NOT_DONE,
        ))

        # Noise (measured in the SPB ROIs): stability against the reference value.
        # The bounds are the ones the verdict uses (qc_history.noise_bounds).
        ref_noise = self.reference_noise
        if nps_results is None:
            measured = "non mesuré (SPB non calculé)"
        else:
            measured = f"{format_fr(nps_results.noise, 2)} UH"
            if ref_noise is not None:
                measured += f"<br/>(écart : {format_fr(nps_results.noise - ref_noise, 2, sign=True)} UH)"
        if ref_noise is not None:
            lower_bound, upper_bound = noise_bounds(ref_noise)
            reference = f"{format_fr(ref_noise, 2)} UH"
            criterion = (f"{format_fr(lower_bound, 2, sign=True)} UH ≤ écart ≤ "
                         f"{format_fr(upper_bound, 2, sign=True)} UH")
        else:
            reference = "absente"
            criterion = ("MIN(-0,2 ; -0,1 × B<sub>réf</sub>) ≤ B - B<sub>réf</sub> ≤ "
                         "MAX(0,2 ; 0,1 × B<sub>réf</sub>)")
        rows.append(("Bruit (stabilité)", measured, reference, criterion, statuses["noise"],
                     NOT_DONE if nps_results is None else NO_REFERENCE))

        # SPB mean frequency: stability against the reference value
        ref_nps = self.reference_nps_freq if self.reference_nps_freq else None
        if nps_results is None:
            measured = "non mesurée"
        else:
            measured = f"{format_fr(nps_results.mean_frequency, 3)} cycles/mm"
            if ref_nps is not None:
                deviation_pct = (nps_results.mean_frequency - ref_nps) / ref_nps * 100
                measured += f"<br/>(écart : {format_fr(deviation_pct, 1, sign=True)} %)"
        if ref_nps is not None:
            lower_bound, upper_bound = nps_bounds(ref_nps)
            reference = f"{format_fr(ref_nps, 3)} cycles/mm"
            criterion = (f"-10 % ≤ écart ≤ +10 %<br/>soit {format_fr(lower_bound, 3)} "
                         f"à {format_fr(upper_bound, 3)} cycles/mm")
        else:
            reference = "absente"
            criterion = "-10 % ≤ (f - f<sub>réf</sub>) / f<sub>réf</sub> ≤ +10 %"
        rows.append(("Fréquence moyenne du SPB (stabilité)", measured, reference, criterion,
                     statuses["nps_freq"], NOT_DONE if nps_results is None else NO_REFERENCE))

        if artifact_result is None:
            measured = "non inspectés"
        else:
            measured = "Présence d'artéfacts" if artifact_result.artifacts_present else "Absence d'artéfacts"
        rows.append(("Artéfacts (inspection visuelle)", measured, "—",
                     "absence d'artéfact (fenêtre : centre 0 UH, largeur 80 UH)",
                     statuses["artifacts"], NOT_DONE))

        header = ["Test", "Valeur mesurée", "Référence / valeur attendue", "Critère d'acceptabilité", "Conformité"]
        data = [[self._para(f"<b>{h}</b>", 'CellSmall') for h in header]]
        for test, measured, reference, criterion, status, pending_text in rows:
            if status == PENDING:
                verdict, style = pending_text, 'ResultPending'
            else:
                verdict, style = _verdict(status)
            data.append([
                self._para(f"<b>{test}</b>", 'CellSmall'),
                self._para(measured, 'CellSmall'),
                self._para(reference, 'CellSmall'),
                self._para(criterion, 'CellSmall'),
                self._para(verdict, style + 'Cell'),
            ])

        table = Table(data, colWidths=[3.4 * cm, 3.3 * cm, 2.6 * cm, 5.0 * cm, 2.7 * cm], repeatRows=1)
        table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
            ('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.grey),
            ('LINEBELOW', (0, 1), (-1, -1), 0.25, colors.lightgrey),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 4))

        return elements


def generate_report_filename(
    device_name: str,
    inventory_number: str,
    date: Optional[datetime] = None,
    control_type: str = DEFAULT_CONTROL_TYPE,
) -> str:
    """
    Generate a standardized PDF report filename.

    Pattern: CQI-control-type_device-name_inventory-number_YYYY-MM-DD.pdf

    Args:
        device_name: Name of the CT device.
        inventory_number: Hospital inventory number.
        date: Date for the report (defaults to today).
        control_type: Type of control (CONTROL_TYPES or free text).

    Returns:
        Sanitized filename string.
    """
    if date is None:
        date = datetime.now()
    elif isinstance(date, str):
        # ISO date (QCRun.run_date) or DICOM study date (YYYYMMDD); None if unusable
        try:
            date = datetime.fromisoformat(date.strip())
        except ValueError:
            iso = dicom_date_to_iso(date)
            date = datetime.fromisoformat(iso) if iso else None

    # Sanitize device name and inventory number for filename
    def sanitize(s: str) -> str:
        # Replace spaces and special chars with hyphens, remove invalid chars
        s = re.sub(r'[<>:"/\\|?*\[\]]', '', s)  # Remove invalid filename chars
        s = re.sub(r'\s+', '-', s.strip())  # Replace spaces with hyphens
        s = re.sub(r'-+', '-', s)  # Collapse multiple hyphens
        return s.strip('-')

    device_clean = sanitize(device_name) or "Unknown"
    inventory_clean = sanitize(inventory_number) or "NoInv"
    # A control without a known date is not named after the day of the export
    date_str = date.strftime('%Y-%m-%d') if date is not None else "date-inconnue"
    # "semestriel (per-opératoire)" → "CQI-semestriel-per-opératoire"
    type_clean = sanitize(re.sub(r'[()]', '', control_type or ""))
    prefix = f"CQI-{type_clean}" if type_clean else "CQI"

    return f"{prefix}_{device_clean}_{inventory_clean}_{date_str}.pdf"


def generate_pdf_report(
    output_path: str | Path,
    image: DicomImage,
    water_results: Optional[WaterPhantomResults] = None,
    nps_results: Optional[NPSResult] = None,
    artifact_result: Optional[ArtifactInspectionResult] = None,
    hospital_name: str = "[Nom de l'établissement]",
    hospital_location: str = "[Localisation de l'équipement]",
    device_name: str = "[Nom de l'équipement]",
    commissioning_date: str = "[Date de mise en service]",
    serial_number: str = "[Numéro de série]",
    inventory_number: str = "[Numéro d'inventaire]",
    reference_noise: Optional[float] = None,
    reference_nps_freq: Optional[float] = None,
    nps_image: Optional[DicomImage] = None,
    logo_path: Optional[str] = None,
    logo_scale: float = 1.0,
    notes: str = "",
    history: Optional[list[QCRun]] = None,
    phantom_brand: str = "",
    phantom_model: str = "",
    phantom_serial: str = "",
    clinical_protocol_origin: str = "",
    reconstruction_algorithm: str = "",
    dicom_folder: str = "",
    control_type: str = DEFAULT_CONTROL_TYPE,
    performed_by: str = "",
    validated_by: str = "",
    hu_slice_index: Optional[int] = None,
    nps_slice_range: Optional[tuple[int, int]] = None,
    total_slices: Optional[int] = None,
    control_date: str = "",
    control_date_manual: bool = False,
):
    """
    Convenience function to generate a PDF report.

    Args:
        output_path: Path to save the PDF.
        image: DicomImage with metadata (used for HU ROI visualization).
        water_results: Water phantom analysis results.
        nps_results: NPS analysis results.
        artifact_result: Artifact inspection result.
        hospital_name: Name of the hospital/establishment.
        hospital_location: Location of the equipment within the hospital.
        device_name: Name of the CT device.
        commissioning_date: Date the device was commissioned.
        serial_number: Device serial number.
        inventory_number: Hospital inventory number.
        reference_noise: Reference noise value (σ) in HU for stability test.
        reference_nps_freq: Reference NPS mean frequency in cycles/mm for stability test.
        nps_image: DicomImage for NPS ROI visualization (middle of NPS range).
        logo_path: Path to logo image (PNG or JPG) to display at the top of the report.
        logo_scale: Logo scale factor (1.0 = 100%).
        notes: User notes in simplified markdown format.
        history: Previously recorded QC runs of the device (table + trend charts).
        phantom_brand, phantom_model, phantom_serial: QC phantom (register item).
        clinical_protocol_origin: Clinical protocol the QC protocol derives from.
        reconstruction_algorithm: Reconstruction algorithm and level of the QC protocol.
        dicom_folder: Folder of the archived DICOM images the results refer to.
        control_type: Type of control printed in the title (CONTROL_TYPES or free text).
        performed_by: Name of the person who performed the control (blank to fill in by hand).
        validated_by: Name of the medical physicist who validates it (blank to fill in by hand).
        hu_slice_index: 0-based index of ``image`` in the series (HU slice).
        nps_slice_range: 0-based (first, last) slices of the NPS analysis, inclusive.
        total_slices: Number of slices of the series.
        control_date: ISO date of the control when the images carry none (typed by the user).
        control_date_manual: True when ``control_date`` was typed by hand.
    """
    generator = PDFReportGenerator(
        hospital_name=hospital_name,
        hospital_location=hospital_location,
        device_name=device_name,
        commissioning_date=commissioning_date,
        serial_number=serial_number,
        inventory_number=inventory_number,
        reference_noise=reference_noise,
        reference_nps_freq=reference_nps_freq,
        logo_path=logo_path,
        logo_scale=logo_scale,
        notes=notes,
        history=history,
        phantom_brand=phantom_brand,
        phantom_model=phantom_model,
        phantom_serial=phantom_serial,
        clinical_protocol_origin=clinical_protocol_origin,
        reconstruction_algorithm=reconstruction_algorithm,
        dicom_folder=dicom_folder,
        control_type=control_type,
        performed_by=performed_by,
        validated_by=validated_by,
    )
    generator.generate_report(
        output_path, image, water_results, nps_results, artifact_result, nps_image,
        hu_slice_index=hu_slice_index, nps_slice_range=nps_slice_range, total_slices=total_slices,
        control_date=control_date, control_date_manual=control_date_manual,
    )
