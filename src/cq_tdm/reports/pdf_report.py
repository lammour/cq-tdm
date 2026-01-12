"""PDF report generation for CT quality control results.

Generates structured PDF reports according to ANSM decision of 18/12/2025.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from io import BytesIO

import numpy as np

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    PageBreak,
    KeepTogether,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from ..core import DicomImage, WaterPhantomResults, NPSResult, format_fr


@dataclass
class ArtifactInspectionResult:
    """Result from artifact inspection."""
    artifacts_present: bool  # True if artifacts detected (NC)
    description: str = ""  # Description of artifacts if present


def _status_text(acceptable: bool, ncg: bool) -> str:
    """Convert status to text."""
    if acceptable:
        return "CONFORME"
    elif ncg:
        return "NON-CONFORMITÉ GRAVE"
    else:
        return "NON CONFORME"


def _action_text(acceptable: bool, ncg: bool) -> str:
    """Get required action text based on conformity status."""
    if acceptable:
        return ""
    elif ncg:
        return "Arrêt de l'exploitation et signalement à l'ANSM et à l'ARS dont dépend l'exploitant dans un délai de 2 jours ouvrés dans le cadre du système national de matériovigilance"
    else:
        return "Remise en conformité dès que possible"


def _status_color(acceptable: bool, ncg: bool) -> colors.Color:
    """Get color for status."""
    if acceptable:
        return colors.green
    elif ncg:
        return colors.red
    else:
        return colors.orange


def _generate_nps_plot(nps_result: NPSResult) -> BytesIO:
    """Generate NPS radial curve plot as PNG in a BytesIO buffer."""
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 4), dpi=150)

    # Plot radial NPS
    ax.plot(
        nps_result.frequencies_radial,
        nps_result.nps_radial,
        'b-',
        linewidth=1.5,
        label='SPB radial'
    )

    # Mark mean frequency
    ax.axvline(
        nps_result.mean_frequency,
        color='r',
        linestyle='--',
        linewidth=1,
        label=f'Fréq. moyenne: {format_fr(nps_result.mean_frequency, 3)} cycles/mm'
    )

    ax.set_xlabel('Fréquence (cycles/mm)')
    ax.set_ylabel('SPB (HU²·mm²)')
    ax.set_title('Spectre de Puissance du Bruit (SPB)')
    ax.legend(loc='upper right', fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)

    plt.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format='PNG', bbox_inches='tight')
    plt.close(fig)
    buffer.seek(0)

    return buffer


def _generate_hu_roi_image(
    image: DicomImage,
    water_results: WaterPhantomResults,
    window: int = 400,
    level: int = 40,
) -> BytesIO:
    """Generate image with HU ROI overlays as PNG in a BytesIO buffer."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    # Apply window/level
    min_val = level - window / 2
    max_val = level + window / 2
    display_array = np.clip(image.pixel_array, min_val, max_val)
    display_array = ((display_array - min_val) / (max_val - min_val) * 255).astype(np.uint8)

    fig, ax = plt.subplots(figsize=(4, 4), dpi=120)
    ax.imshow(display_array, cmap='gray', aspect='equal')

    # All UH ROIs in yellow
    roi_color = '#ffcc00'

    # Draw central ROI
    central = water_results.central
    circle = Circle(
        (central.center_col, central.center_row),
        central.radius,
        fill=False,
        edgecolor=roi_color,
        linewidth=2,
    )
    ax.add_patch(circle)

    # Draw peripheral ROIs (all in yellow)
    for roi_measurement in [water_results.top, water_results.right,
                            water_results.bottom, water_results.left]:
        circle = Circle(
            (roi_measurement.center_col, roi_measurement.center_row),
            roi_measurement.radius,
            fill=False,
            edgecolor=roi_color,
            linewidth=2,
        )
        ax.add_patch(circle)

    ax.set_title('ROI Nombre CT / Uniformité', fontsize=9, fontweight='bold')
    ax.axis('off')

    plt.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format='PNG', bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buffer.seek(0)

    return buffer


def _generate_nps_roi_image(
    image: DicomImage,
    nps_results: NPSResult,
    window: int = 400,
    level: int = 40,
) -> BytesIO:
    """Generate image with NPS ROI overlays as PNG in a BytesIO buffer."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    # Apply window/level
    min_val = level - window / 2
    max_val = level + window / 2
    display_array = np.clip(image.pixel_array, min_val, max_val)
    display_array = ((display_array - min_val) / (max_val - min_val) * 255).astype(np.uint8)

    fig, ax = plt.subplots(figsize=(4, 4), dpi=120)
    ax.imshow(display_array, cmap='gray', aspect='equal')

    # All NPS ROIs in green
    roi_color = '#00cc00'
    for roi_pos in nps_results.roi_config.rois:
        half_size = roi_pos.side_square / 2
        rect = Rectangle(
            (roi_pos.x - half_size, roi_pos.y - half_size),
            roi_pos.side_square,
            roi_pos.side_square,
            fill=False,
            edgecolor=roi_color,
            linewidth=1.5,
        )
        ax.add_patch(rect)

    ax.set_title('ROI Spectre de puissance du bruit', fontsize=9, fontweight='bold')
    ax.axis('off')

    plt.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format='PNG', bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buffer.seek(0)

    return buffer


def _generate_artifact_image(
    image: DicomImage,
    window: int = 80,
    level: int = 0,
) -> BytesIO:
    """Generate image with ANSM artifact inspection window settings as PNG.

    ANSM requires: Window center (L) = 0 HU, Window width (W) = 80 HU
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    # Apply ANSM window/level for artifact inspection
    min_val = level - window / 2  # -40 HU
    max_val = level + window / 2  # +40 HU
    display_array = np.clip(image.pixel_array, min_val, max_val)
    display_array = ((display_array - min_val) / (max_val - min_val) * 255).astype(np.uint8)

    fig, ax = plt.subplots(figsize=(5, 5), dpi=120)
    ax.imshow(display_array, cmap='gray', aspect='equal')

    ax.set_title('Inspection artéfacts (L=0, W=80 HU)', fontsize=9, fontweight='bold')
    ax.axis('off')

    plt.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format='PNG', bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buffer.seek(0)

    return buffer


def _create_status_badge(status_text: str, status_color: colors.Color) -> Table:
    """Create a colored status badge as a small table."""
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


def _draw_footer(canvas_obj: canvas.Canvas, doc, page_num: int):
    """Draw footer with page number."""
    canvas_obj.saveState()
    canvas_obj.setFont('Helvetica', 9)
    canvas_obj.setFillColor(colors.Color(0.5, 0.5, 0.5))

    # Page number centered at bottom
    page_text = f"Page {page_num}"
    canvas_obj.drawCentredString(A4[0] / 2, 1.5 * cm, page_text)

    canvas_obj.restoreState()


def _draw_header(canvas_obj: canvas.Canvas, doc, hospital_name: str, device_name: str, inventory_number: str, control_datetime: str):
    """Draw header with hospital name, device name with inventory number, and control date."""
    canvas_obj.saveState()
    canvas_obj.setFont('Helvetica', 8)
    canvas_obj.setFillColor(colors.Color(0.4, 0.4, 0.4))

    # Build device display string with inventory number
    device_display = device_name or "—"
    if inventory_number:
        device_display = f"{device_display} ({inventory_number})"

    # Header line at top (use placeholders for empty values)
    y_pos = A4[1] - 1.2 * cm
    canvas_obj.drawString(2 * cm, y_pos, hospital_name or "—")
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
    ):
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
            fontSize=20,
            alignment=TA_CENTER,
            spaceAfter=16,
            textColor=colors.Color(0.2, 0.2, 0.3),
        ))

        self.styles.add(ParagraphStyle(
            name='ReportSubtitle',
            fontName=base_font,
            fontSize=14,
            alignment=TA_CENTER,
            spaceAfter=4,
            textColor=colors.Color(0.4, 0.4, 0.5),
        ))

        self.styles.add(ParagraphStyle(
            name='SectionTitle',
            fontName=bold_font,
            fontSize=13,
            spaceBefore=16,
            spaceAfter=8,
            textColor=colors.Color(0.2, 0.2, 0.4),
            borderPadding=0,
        ))

        self.styles.add(ParagraphStyle(
            name='SubSection',
            fontName=bold_font,
            fontSize=11,
            spaceBefore=10,
            spaceAfter=4,
            textColor=colors.Color(0.3, 0.3, 0.4),
        ))

        self.styles.add(ParagraphStyle(
            name='InfoText',
            fontName=base_font,
            fontSize=10,
            leading=14,
        ))

        self.styles.add(ParagraphStyle(
            name='ResultOK',
            fontName=bold_font,
            fontSize=10,
            textColor=colors.Color(0, 0.5, 0),  # Green
        ))

        self.styles.add(ParagraphStyle(
            name='ResultNC',
            fontName=bold_font,
            fontSize=10,
            textColor=colors.orange,
        ))

        self.styles.add(ParagraphStyle(
            name='ResultNCG',
            fontName=bold_font,
            fontSize=10,
            textColor=colors.red,
        ))

        self.styles.add(ParagraphStyle(
            name='Footer',
            fontName=base_font,
            fontSize=8,
            textColor=colors.Color(0.5, 0.5, 0.5),
            alignment=TA_CENTER,
        ))

    def generate_report(
        self,
        output_path: str | Path,
        image: DicomImage,
        water_results: Optional[WaterPhantomResults] = None,
        nps_results: Optional[NPSResult] = None,
        artifact_result: Optional[ArtifactInspectionResult] = None,
        nps_image: Optional[DicomImage] = None,
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
        """
        output_path = Path(output_path)

        # Store control datetime for header
        control_datetime = datetime.now().strftime('%d/%m/%Y %H:%M')

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2.5 * cm,  # Extra space for header on pages 2+
            bottomMargin=2 * cm,
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

                logo_img = Image(self.logo_path, width=final_width, height=final_height)
                logo_img.hAlign = 'CENTER'
                story.append(logo_img)
                story.append(Spacer(1, 10))
            except Exception:
                pass  # Skip logo if file cannot be loaded

        # Title
        story.append(Paragraph(
            "Rapport de contrôle qualité",
            self.styles['ReportTitle']
        ))
        story.append(Paragraph(
            "Contrôle de qualité des tomodensitomètres",
            self.styles['ReportSubtitle']
        ))
        story.append(Paragraph(
            "Décision ANSM du 18/12/2025",
            self.styles['ReportSubtitle']
        ))
        story.append(Paragraph(
            f"Contrôle de qualité interne trimestriel du {control_datetime}",
            self.styles['ReportSubtitle']
        ))
        story.append(Spacer(1, 12))

        # Summary badge at the top
        overall_status, overall_color, overall_action = self._compute_overall_status(
            water_results, nps_results, artifact_result
        )
        story.append(_create_status_badge(overall_status, overall_color))
        if overall_action:
            story.append(Spacer(1, 6))
            story.append(Paragraph(
                f"<b>{overall_action}</b>",
                ParagraphStyle(
                    'ActionText',
                    parent=self.styles['InfoText'],
                    alignment=TA_CENTER,
                    textColor=overall_color,
                    fontSize=11,
                )
            ))
        story.append(Spacer(1, 16))

        # Summary section at the beginning
        story.append(KeepTogether(self._build_summary_section(water_results, nps_results, artifact_result)))

        # Equipment info section
        story.append(KeepTogether(self._build_equipment_section(image)))

        # Acquisition parameters section
        story.append(KeepTogether(self._build_acquisition_section(image)))

        # ROI visualization (if any results available)
        if water_results or nps_results:
            story.append(KeepTogether(self._build_roi_visualization_section(
                image, water_results, nps_results, nps_image
            )))

        # Water phantom results (Nombre CT, Uniformité, Bruit)
        if water_results:
            story.append(KeepTogether(self._build_ct_number_section(water_results)))
            story.append(KeepTogether(self._build_uniformity_section(water_results)))
            story.append(KeepTogether(self._build_noise_section(water_results)))

        # NPS results (without graph)
        if nps_results:
            story.append(KeepTogether(self._build_nps_results_section(nps_results)))

        # Artifact inspection results
        story.append(KeepTogether(self._build_artifact_section(image, artifact_result)))

        # Notes section (if any)
        if self.notes and self.notes.strip():
            story.append(KeepTogether(self._build_notes_section()))

        # Content footer (software info)
        story.append(Spacer(1, 30))
        story.append(Paragraph(
            "Rapport généré par CQ TDM v0.2.3",
            self.styles['Footer']
        ))

        # Page counter for callbacks
        page_counter = [0]

        def on_first_page(canvas_obj, doc):
            """First page: footer only, no header."""
            page_counter[0] = 1
            _draw_footer(canvas_obj, doc, page_counter[0])

        def on_later_pages(canvas_obj, doc):
            """Subsequent pages: header and footer."""
            page_counter[0] += 1
            _draw_header(canvas_obj, doc, self.hospital_name, self.device_name, self.inventory_number, control_datetime)
            _draw_footer(canvas_obj, doc, page_counter[0])

        doc.build(story, onFirstPage=on_first_page, onLaterPages=on_later_pages)

    def _compute_overall_status(
        self,
        water_results: Optional[WaterPhantomResults],
        nps_results: Optional[NPSResult],
        artifact_result: Optional[ArtifactInspectionResult],
    ) -> tuple[str, colors.Color, str]:
        """Compute overall status, color, and required action."""
        all_acceptable = True
        any_ncg = False

        if water_results:
            if not water_results.water_ct_acceptable:
                all_acceptable = False
                if water_results.water_ct_ncg:
                    any_ncg = True
            if not water_results.uniformity_acceptable:
                all_acceptable = False
                if water_results.uniformity_ncg:
                    any_ncg = True

            # Noise stability check if reference available
            if self.reference_noise is not None:
                deviation = water_results.noise - self.reference_noise
                lower_bound = min(-0.2, -0.1 * self.reference_noise)
                upper_bound = max(0.2, 0.1 * self.reference_noise)
                if not (lower_bound <= deviation <= upper_bound):
                    all_acceptable = False

        # NPS stability check if reference available
        if nps_results and self.reference_nps_freq is not None and self.reference_nps_freq > 0:
            deviation_pct = ((nps_results.mean_frequency - self.reference_nps_freq) / self.reference_nps_freq) * 100
            if not (-10.0 <= deviation_pct <= 10.0):
                all_acceptable = False

        if artifact_result is not None and artifact_result.artifacts_present:
            all_acceptable = False

        if all_acceptable:
            return "CONFORME", colors.Color(0, 0.6, 0), ""
        elif any_ncg:
            return "NON-CONFORMITÉ GRAVE", colors.red, "Arrêt de l'exploitation et signalement à l'ANSM et à l'ARS dont dépend l'exploitant dans un délai de 2 jours ouvrés dans le cadre du système national de matériovigilance"
        else:
            return "NON CONFORME", colors.orange, "Remise en conformité dès que possible"

    def _build_equipment_section(self, image: DicomImage) -> list:
        """Build equipment information section."""
        elements = []

        elements.append(Paragraph("Installation", self.styles['SectionTitle']))

        # Helper to display empty fields as "Non renseigné"
        def val(s: str) -> str:
            return s if s else "Non renseigné"

        data = [
            ["Établissement :", val(self.hospital_name)],
            ["Localisation :", val(self.hospital_location)],
            ["Fabricant :", image.manufacturer or "N/A"],
            ["Modèle :", image.model_name or "N/A"],
            ["Date de mise en service :", val(self.commissioning_date)],
            ["Numéro de série :", val(self.serial_number)],
            ["Numéro d'inventaire :", val(self.inventory_number)],
        ]

        table = Table(data, colWidths=[4.5 * cm, 10 * cm])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ]))

        elements.append(table)
        elements.append(Spacer(1, 8))

        return elements

    def _build_acquisition_section(self, image: DicomImage) -> list:
        """Build acquisition parameters section."""
        elements = []

        elements.append(Paragraph("Paramètres d'acquisition", self.styles['SectionTitle']))

        # Format date
        date_str = image.study_date
        if len(date_str) == 8:
            date_str = f"{date_str[6:8]}/{date_str[4:6]}/{date_str[:4]}"

        data = [
            ["Date d'acquisition :", date_str or "N/A"],
            ["Protocole :", image.series_description or "N/A"],
            ["Tension (kV) :", f"{image.kvp:.0f}" if image.kvp else "N/A"],
            ["Courant (mA) :", f"{image.tube_current:.0f}" if image.tube_current else "N/A"],
            ["Filtre :", image.convolution_kernel or "N/A"],
            ["Épaisseur coupe :", f"{format_fr(image.slice_thickness, 1)} mm" if image.slice_thickness else "N/A"],
            ["Taille pixel :", f"{format_fr(image.pixel_size_mm, 3)} mm"],
            ["FOV :", f"{image.fov:.0f} mm" if image.fov else "N/A"],
        ]

        table = Table(data, colWidths=[4 * cm, 10 * cm])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ]))

        elements.append(table)
        elements.append(Spacer(1, 8))

        return elements

    def _build_roi_visualization_section(
        self,
        image: DicomImage,
        water_results: Optional[WaterPhantomResults] = None,
        nps_results: Optional[NPSResult] = None,
        nps_image: Optional[DicomImage] = None,
    ) -> list:
        """Build ROI visualization section with side-by-side images."""
        elements = []

        elements.append(Paragraph("Visualisation des ROI", self.styles['SectionTitle']))

        # Build table with images side by side
        images_row = []

        if water_results:
            try:
                hu_buffer = _generate_hu_roi_image(image, water_results)
                hu_img = Image(hu_buffer, width=7.5 * cm, height=7.5 * cm)
                images_row.append(hu_img)
            except Exception:
                images_row.append(Paragraph("Image UH non disponible", self.styles['InfoText']))

        if nps_results and nps_results.roi_config.rois:
            try:
                # Use nps_image if provided (middle of NPS range), otherwise fallback to main image
                nps_display_image = nps_image if nps_image is not None else image
                nps_buffer = _generate_nps_roi_image(nps_display_image, nps_results)
                nps_img = Image(nps_buffer, width=7.5 * cm, height=7.5 * cm)
                images_row.append(nps_img)
            except Exception:
                images_row.append(Paragraph("Image SPB non disponible", self.styles['InfoText']))

        if images_row:
            # Create table with images
            if len(images_row) == 2:
                img_table = Table([images_row], colWidths=[8 * cm, 8 * cm])
            else:
                img_table = Table([images_row], colWidths=[8 * cm])

            img_table.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            elements.append(img_table)

        elements.append(Spacer(1, 10))

        return elements

    def _build_artifact_section(
        self,
        image: DicomImage,
        artifact_result: Optional[ArtifactInspectionResult],
    ) -> list:
        """Build artifact inspection results section with image."""
        elements = []

        elements.append(Paragraph("Inspection des Artéfacts", self.styles['SectionTitle']))
        elements.append(Paragraph(
            "Fenêtre ANSM : Centre (L) = 0 UH, Largeur (W) = 80 UH",
            self.styles['InfoText']
        ))
        elements.append(Spacer(1, 6))

        # Add artifact inspection image
        try:
            artifact_buffer = _generate_artifact_image(image)
            artifact_img = Image(artifact_buffer, width=12 * cm, height=12 * cm)
            artifact_img.hAlign = 'CENTER'
            elements.append(artifact_img)
            elements.append(Spacer(1, 8))
        except Exception:
            pass  # Skip image if generation fails

        if artifact_result is None:
            elements.append(Paragraph(
                "Statut : Non inspecté",
                self.styles['InfoText']
            ))
        else:
            if artifact_result.artifacts_present:
                observation = "Présence d'artéfacts"
                status_text = "NON CONFORME"
                status_style = 'ResultNC'
            else:
                observation = "Absence d'artéfacts"
                status_text = "CONFORME"
                status_style = 'ResultOK'

            data = [["Observation :", observation]]
            if artifact_result.artifacts_present and artifact_result.description:
                data.append(["Description :", artifact_result.description])

            table = Table(data, colWidths=[4 * cm, 10 * cm])
            table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ]))
            elements.append(table)
            elements.append(Spacer(1, 4))
            elements.append(Paragraph(f"Résultat : {status_text}", self.styles[status_style]))
            if artifact_result.artifacts_present:
                elements.append(Paragraph(f"<i>→ {_action_text(False, False)}</i>", self.styles[status_style]))

        elements.append(Spacer(1, 8))
        return elements

    def _build_notes_section(self) -> list:
        """Build notes section with simplified markdown rendering."""
        import re

        elements = []

        elements.append(Paragraph("Notes", self.styles['SectionTitle']))

        # Parse simplified markdown and convert to paragraphs
        lines = self.notes.split('\n')
        current_list_items = []

        def flush_list():
            """Flush accumulated list items into a table."""
            nonlocal current_list_items
            if current_list_items:
                # Create a bullet list using a table
                list_data = [[f"• {item}"] for item in current_list_items]
                list_table = Table(list_data, colWidths=[14 * cm])
                list_table.setStyle(TableStyle([
                    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                    ('FONTSIZE', (0, 0), (-1, -1), 10),
                    ('LEFTPADDING', (0, 0), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                    ('TOPPADDING', (0, 0), (-1, -1), 2),
                ]))
                elements.append(list_table)
                current_list_items = []

        def parse_inline_formatting(text: str) -> str:
            """Convert **bold** and *italic* to HTML tags."""
            # Bold: **text**
            text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
            # Italic: *text* (but not inside bold)
            text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<i>\1</i>', text)
            return text

        for line in lines:
            stripped = line.strip()

            if not stripped:
                flush_list()
                elements.append(Spacer(1, 4))
                continue

            # Check for heading (## Title)
            if stripped.startswith('## '):
                flush_list()
                heading_text = parse_inline_formatting(stripped[3:])
                elements.append(Paragraph(heading_text, self.styles['SubSection']))
                continue

            # Check for list item (- item)
            if stripped.startswith('- '):
                item_text = parse_inline_formatting(stripped[2:])
                current_list_items.append(item_text)
                continue

            # Regular paragraph
            flush_list()
            para_text = parse_inline_formatting(stripped)
            elements.append(Paragraph(para_text, self.styles['InfoText']))

        # Flush any remaining list items
        flush_list()

        elements.append(Spacer(1, 8))
        return elements

    def _get_status_style(self, acceptable: bool, ncg: bool) -> str:
        """Get the style name for a status."""
        if acceptable:
            return 'ResultOK'
        elif ncg:
            return 'ResultNCG'
        else:
            return 'ResultNC'

    def _build_ct_number_section(self, results: WaterPhantomResults) -> list:
        """Build CT number section."""
        elements = []

        elements.append(Paragraph("Nombre CT de l'eau", self.styles['SectionTitle']))

        status_text = _status_text(results.water_ct_acceptable, results.water_ct_ncg)
        status_style = self._get_status_style(results.water_ct_acceptable, results.water_ct_ncg)

        data = [
            ["Valeur mesurée :", f"{format_fr(results.water_ct_number, 1, sign=True)} HU"],
            ["Valeur attendue :", "0 HU"],
            ["Critère NC :", "±7 HU"],
            ["Critère NCG :", "±25 HU"],
        ]

        table = Table(data, colWidths=[4 * cm, 10 * cm])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(f"Résultat : {status_text}", self.styles[status_style]))
        action = _action_text(results.water_ct_acceptable, results.water_ct_ncg)
        if action:
            elements.append(Paragraph(f"<i>→ {action}</i>", self.styles[status_style]))
        elements.append(Spacer(1, 8))

        return elements

    def _build_uniformity_section(self, results: WaterPhantomResults) -> list:
        """Build uniformity section."""
        elements = []

        elements.append(Paragraph("Uniformité", self.styles['SectionTitle']))

        status_text = _status_text(results.uniformity_acceptable, results.uniformity_ncg)
        status_style = self._get_status_style(results.uniformity_acceptable, results.uniformity_ncg)

        # Individual peripheral deviations table
        central_mean = results.central.mean_hu
        peripheral_data = [
            ("12h (Haut)", results.top.mean_hu, results.top.mean_hu - central_mean),
            ("3h (Droite)", results.right.mean_hu, results.right.mean_hu - central_mean),
            ("6h (Bas)", results.bottom.mean_hu, results.bottom.mean_hu - central_mean),
            ("9h (Gauche)", results.left.mean_hu, results.left.mean_hu - central_mean),
        ]

        data = [["Position", "Moyenne (HU)", "Écart vs Centre (HU)"]]
        for name, mean_hu, deviation in peripheral_data:
            data.append([name, format_fr(mean_hu, 1, sign=True), format_fr(deviation, 1, sign=True)])

        table = Table(data, colWidths=[4 * cm, 4.5 * cm, 5 * cm])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.92, 0.92, 0.96)),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.Color(0.75, 0.75, 0.75)),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 6))

        summary_data = [
            ["Écart max centre-périphérie :", f"{format_fr(results.uniformity, 1)} HU"],
            ["Critère NC :", "≤ 7 HU"],
            ["Critère NCG :", "> 25 HU"],
        ]
        summary_table = Table(summary_data, colWidths=[5 * cm, 8.5 * cm])
        summary_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(f"Résultat : {status_text}", self.styles[status_style]))
        action = _action_text(results.uniformity_acceptable, results.uniformity_ncg)
        if action:
            elements.append(Paragraph(f"<i>→ {action}</i>", self.styles[status_style]))
        elements.append(Spacer(1, 8))

        return elements

    def _build_noise_section(self, results: WaterPhantomResults) -> list:
        """Build noise section."""
        elements = []

        elements.append(Paragraph("Bruit", self.styles['SectionTitle']))

        data = [
            ["Écart-type central :", f"{format_fr(results.noise, 1)} HU"],
        ]

        # Add stability comparison if reference value is available
        is_conforme = None
        if self.reference_noise is not None:
            deviation = results.noise - self.reference_noise
            # ANSM criterion: MIN(-0.2, -0.1*B_ref) ≤ (B_i - B_ref) ≤ MAX(0.2, 0.1*B_ref)
            lower_bound = min(-0.2, -0.1 * self.reference_noise)
            upper_bound = max(0.2, 0.1 * self.reference_noise)
            is_conforme = lower_bound <= deviation <= upper_bound

            data.append(["Valeur de référence :", f"{format_fr(self.reference_noise, 1)} HU"])
            data.append(["Écart :", f"{format_fr(deviation, 2, sign=True)} HU"])
            data.append(["Critère :", f"[{format_fr(lower_bound, 2, sign=True)}, {format_fr(upper_bound, 2, sign=True)}] HU"])

        table = Table(data, colWidths=[4 * cm, 10 * cm])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(table)

        # Add conformity result if reference is available
        if is_conforme is not None:
            elements.append(Spacer(1, 4))
            if is_conforme:
                elements.append(Paragraph("Résultat : CONFORME", self.styles['ResultOK']))
            else:
                elements.append(Paragraph("Résultat : NON CONFORME", self.styles['ResultNC']))
                elements.append(Paragraph(f"<i>→ {_action_text(False, False)}</i>", self.styles['ResultNC']))

        elements.append(Spacer(1, 8))

        return elements

    def _build_nps_results_section(self, results: NPSResult) -> list:
        """Build NPS results section."""
        elements = []

        elements.append(Paragraph("Spectre de Puissance du Bruit (SPB)", self.styles['SectionTitle']))

        data = [
            ["Nombre de coupes analysées :", str(results.num_slices)],
            ["Fréquence moyenne :", f"{format_fr(results.mean_frequency, 3)} cycles/mm"],
        ]

        # Add stability comparison if reference value is available
        is_conforme = None
        if self.reference_nps_freq is not None and self.reference_nps_freq > 0:
            deviation_pct = ((results.mean_frequency - self.reference_nps_freq) / self.reference_nps_freq) * 100
            is_conforme = -10.0 <= deviation_pct <= 10.0

            data.append(["Fréquence de référence :", f"{format_fr(self.reference_nps_freq, 3)} cycles/mm"])
            data.append(["Écart :", f"{format_fr(deviation_pct, 1, sign=True)} %"])
            data.append(["Critère :", "±10 %"])

        table = Table(data, colWidths=[6 * cm, 8 * cm])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ]))

        elements.append(table)

        # Add conformity result if reference is available
        if is_conforme is not None:
            elements.append(Spacer(1, 4))
            if is_conforme:
                elements.append(Paragraph("Résultat : CONFORME", self.styles['ResultOK']))
            else:
                elements.append(Paragraph("Résultat : NON CONFORME", self.styles['ResultNC']))
                elements.append(Paragraph(f"<i>→ {_action_text(False, False)}</i>", self.styles['ResultNC']))

        elements.append(Spacer(1, 8))

        # Add NPS plot
        try:
            nps_plot_buffer = _generate_nps_plot(results)
            nps_plot_img = Image(nps_plot_buffer, width=14 * cm, height=9.3 * cm)
            nps_plot_img.hAlign = 'CENTER'
            elements.append(nps_plot_img)
            elements.append(Spacer(1, 8))
        except Exception:
            pass  # Skip plot if generation fails

        # Warning for insufficient slices (ANSM requires 10 slices)
        if results.num_slices < 10:
            warning_text = (
                f"<b>Attention :</b> L'ANSM recommande d'analyser 10 coupes centrées sur la coupe centrale. "
                f"Seulement {results.num_slices} coupe(s) utilisée(s)."
            )
            elements.append(Paragraph(warning_text, self.styles['ResultNC']))
            elements.append(Spacer(1, 6))

        # ROI uniformity warnings (if any)
        if results.roi_warnings:
            elements.append(Paragraph("Avertissements", self.styles['SubSection']))

            warning_data = [["Coupe", "ROI", "Description"]]
            for warning in results.roi_warnings:
                warning_data.append([
                    str(warning.slice_index + 1),
                    str(warning.roi_index + 1),
                    warning.message.split(": ", 1)[-1] if ": " in warning.message else warning.message,
                ])

            warning_table = Table(warning_data, colWidths=[2 * cm, 2 * cm, 10 * cm])
            warning_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('BACKGROUND', (0, 0), (-1, 0), colors.Color(1, 0.92, 0.85)),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.Color(0.75, 0.75, 0.75)),
                ('ALIGN', (0, 0), (1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
            ]))

            elements.append(warning_table)

        elements.append(Spacer(1, 8))

        return elements

    def _build_summary_section(
        self,
        water_results: Optional[WaterPhantomResults],
        nps_results: Optional[NPSResult],
        artifact_result: Optional[ArtifactInspectionResult] = None,
    ) -> list:
        """Build summary section with one line per test."""
        elements = []

        elements.append(Paragraph("Résumé", self.styles['SectionTitle']))

        # Build summary lines for each test
        summary_lines = []

        if water_results:
            # CT Number
            ct_status = _status_text(water_results.water_ct_acceptable, water_results.water_ct_ncg)
            ct_style = self._get_status_style(water_results.water_ct_acceptable, water_results.water_ct_ncg)
            summary_lines.append(("Nombre CT de l'eau", f"{format_fr(water_results.water_ct_number, 1, sign=True)} HU", ct_status, ct_style))

            # Uniformity
            unif_status = _status_text(water_results.uniformity_acceptable, water_results.uniformity_ncg)
            unif_style = self._get_status_style(water_results.uniformity_acceptable, water_results.uniformity_ncg)
            summary_lines.append(("Uniformité", f"{format_fr(water_results.uniformity, 1)} HU", unif_status, unif_style))

            # Noise - with conformity check if reference value available
            if self.reference_noise is not None:
                deviation = water_results.noise - self.reference_noise
                lower_bound = min(-0.2, -0.1 * self.reference_noise)
                upper_bound = max(0.2, 0.1 * self.reference_noise)
                noise_conforme = lower_bound <= deviation <= upper_bound
                noise_status = "CONFORME" if noise_conforme else "NON CONFORME"
                noise_style = 'ResultOK' if noise_conforme else 'ResultNC'
                summary_lines.append(("Bruit (stabilité)", f"{format_fr(water_results.noise, 1)} HU", noise_status, noise_style))
            else:
                summary_lines.append(("Bruit", f"{format_fr(water_results.noise, 1)} HU", "—", 'InfoText'))

        if nps_results:
            # NPS - with conformity check if reference value available
            if self.reference_nps_freq is not None and self.reference_nps_freq > 0:
                deviation_pct = ((nps_results.mean_frequency - self.reference_nps_freq) / self.reference_nps_freq) * 100
                nps_conforme = -10.0 <= deviation_pct <= 10.0
                nps_status = "CONFORME" if nps_conforme else "NON CONFORME"
                nps_style = 'ResultOK' if nps_conforme else 'ResultNC'
                summary_lines.append(("SPB (stabilité)", f"{format_fr(nps_results.mean_frequency, 3)} cycles/mm", nps_status, nps_style))
            else:
                summary_lines.append(("SPB - Fréquence moyenne", f"{format_fr(nps_results.mean_frequency, 3)} cycles/mm", "—", 'InfoText'))

        if artifact_result is not None:
            if artifact_result.artifacts_present:
                art_status = "NON CONFORME"
                art_style = 'ResultNC'
            else:
                art_status = "CONFORME"
                art_style = 'ResultOK'
            summary_lines.append(("Artéfacts", "Présence" if artifact_result.artifacts_present else "Absence", art_status, art_style))

        # Create summary table
        if summary_lines:
            for test_name, value, status, style in summary_lines:
                line_parts = [
                    Paragraph(f"<b>{test_name}</b> : {value}", self.styles['InfoText']),
                ]
                if status != "—":
                    line_parts.append(Paragraph(f" → {status}", self.styles[style]))
                else:
                    line_parts.append(Paragraph("", self.styles['InfoText']))

                row_table = Table([line_parts], colWidths=[8.5 * cm, 5.5 * cm])
                row_table.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 0),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                    ('TOPPADDING', (0, 0), (-1, -1), 2),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ]))
                elements.append(row_table)

        elements.append(Spacer(1, 8))

        return elements


def generate_report_filename(
    device_name: str,
    inventory_number: str,
    date: Optional[datetime] = None,
) -> str:
    """
    Generate a standardized PDF report filename.

    Pattern: CQI-trimestriel_device-name_inventory-number_YYYY-MM-DD.pdf

    Args:
        device_name: Name of the CT device.
        inventory_number: Hospital inventory number.
        date: Date for the report (defaults to today).

    Returns:
        Sanitized filename string.
    """
    import re

    if date is None:
        date = datetime.now()

    # Sanitize device name and inventory number for filename
    def sanitize(s: str) -> str:
        # Replace spaces and special chars with hyphens, remove invalid chars
        s = re.sub(r'[<>:"/\\|?*\[\]]', '', s)  # Remove invalid filename chars
        s = re.sub(r'\s+', '-', s.strip())  # Replace spaces with hyphens
        s = re.sub(r'-+', '-', s)  # Collapse multiple hyphens
        return s.strip('-')

    device_clean = sanitize(device_name) or "Unknown"
    inventory_clean = sanitize(inventory_number) or "NoInv"
    date_str = date.strftime('%Y-%m-%d')

    return f"CQI-trimestriel_{device_clean}_{inventory_clean}_{date_str}.pdf"


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
    )
    generator.generate_report(output_path, image, water_results, nps_results, artifact_result, nps_image)
