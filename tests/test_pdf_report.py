"""PDF report: layout, wording and robustness (no ANSM data needed)."""

import inspect
import re
from types import SimpleNamespace

import pytest

from cq_tdm import __version__
from cq_tdm.core.dicom_loader import DicomSeries
from cq_tdm.core.nps import analyze_nps
from cq_tdm.core.qc_history import NC_OR_NCG_NOTICE, QCRun, noise_bounds, nps_bounds
from cq_tdm.core.utils import format_fr
from cq_tdm.core.water_phantom import (
    ROIMeasurement,
    WaterPhantomResults,
    analyze_water_phantom,
    calculate_rois,
)
from cq_tdm.core.qc_history import CONTROL_TYPES
from cq_tdm.reports import pdf_report
from cq_tdm.reports.pdf_report import (
    ArtifactInspectionResult,
    PDFReportGenerator,
    generate_pdf_report,
    generate_report_filename,
)

from .test_phantom_detection import make_phantom

NOTES = """Fantôme centré au laser.

## Points d'attention
- **À surveiller** : bruit proche de la limite
- Tube remplacé le *12/02/2026* & étalonnage <air>
"""


def _texts(flowables) -> list[str]:
    """Every string of a flowable list, nested tables and kept-together blocks included.

    Non-breaking spaces (French typography) are returned as plain spaces.
    """
    out = []
    for f in flowables:
        if isinstance(f, (list, tuple)):
            out.extend(_texts(f))
        elif isinstance(f, str):
            out.append(f)
        elif hasattr(f, "_cellvalues"):
            for row in f._cellvalues:
                out.extend(_texts(row))
        elif hasattr(f, "_content"):
            out.extend(_texts(f._content))
        elif hasattr(f, "text"):
            out.append(f.text)
    return [text.replace("\N{NO-BREAK SPACE}", " ") for text in out]


def _run(**kw) -> QCRun:
    base = dict(run_date="2026-03-15", series_uid="1.2.3", kvp=120, mas=200,
                water_ct=1.0, uniformity=2.0, noise=3.4, nps_freq=0.31,
                artifacts_present=False, ref_noise=3.4, ref_nps_freq=0.31)
    base.update(kw)
    return QCRun(**base)


def _water(ct=0.0, uniformity=1.0) -> WaterPhantomResults:
    roi = ROIMeasurement("r", 0, 0, 5, ct, 2.0, ct - 1, ct + 1, 100)
    return WaterPhantomResults(central=roi, top=roi, right=roi, bottom=roi, left=roi,
                               water_ct_number=ct, uniformity=uniformity)


def _nps(noise=3.4, freq=0.31):
    return SimpleNamespace(mean_frequency=freq, num_slices=10, noise=noise, noise_roi_count=80)


@pytest.fixture(scope="module")
def control():
    """A small analysed series: image, water results, SPB results."""
    pdf_report._ensure_reportlab()
    series = DicomSeries(images=[make_phantom(seed=i) for i in range(4)])
    for i, image in enumerate(series.images):
        image.slice_location = 10.0 + 2.5 * i
        image.acquisition_date = "20260315"
        image.series_instance_uid = "1.2.826.0.1.3680043.2.1125.1"
    image = series.images[1]
    water = analyze_water_phantom(image, calculate_rois(image))
    nps = analyze_nps(series, slice_range=(0, 3), geometry=water.geometry)
    return SimpleNamespace(series=series, image=image, water=water, nps=nps)


@pytest.fixture
def footers(monkeypatch):
    """Footer text of every page drawn by the report."""
    drawn = []
    monkeypatch.setattr(pdf_report, "_draw_footer", lambda canvas_obj, text: drawn.append(text))
    return drawn


@pytest.fixture
def story(monkeypatch):
    """Flowables handed to reportlab by generate_report (the PDF is still built)."""
    pdf_report._ensure_reportlab()
    captured = []

    class Recorder(pdf_report.SimpleDocTemplate):
        def build(self, flowables, **kwargs):
            captured.extend(flowables)
            super().build(flowables, **kwargs)

    monkeypatch.setattr(pdf_report, "SimpleDocTemplate", Recorder)
    return captured


def _page_count(path) -> int:
    return len(re.findall(rb"/Type\s*/Page\b", path.read_bytes()))


# --- U-35 / C-46: footer -----------------------------------------------------

def test_footer_gives_version_dates_and_page_n_of_total(tmp_path, control, footers):
    out = tmp_path / "rapport.pdf"
    history = [_run(series_uid=str(i), run_date=f"2025-{i:02d}-01") for i in range(1, 9)]
    generate_pdf_report(
        out, control.image, control.water, control.nps, ArtifactInspectionResult(False),
        reference_noise=control.nps.noise, reference_nps_freq=control.nps.mean_frequency,
        history=history, notes=NOTES, nps_image=control.series.images[2],
        hu_slice_index=1, nps_slice_range=(0, 3), total_slices=4)

    total = _page_count(out)
    assert len(footers) == total
    for number, text in enumerate(footers, start=1):
        assert re.fullmatch(
            rf"CQ TDM {re.escape(__version__)} · contrôle du 15/03/2026 · "
            rf"édité le \d\d/\d\d/\d{{4}} \d\d:\d\d · page {number} / {total}", text), text
    # U-36: a complete control with history and notes no longer takes 7 pages
    assert total <= 5


# --- U-37: summary table -----------------------------------------------------

def test_summary_table_gives_reference_criterion_and_verdict():
    pdf_report._ensure_reportlab()
    gen = PDFReportGenerator(reference_noise=3.4, reference_nps_freq=0.31)
    section = gen._build_summary_section(
        _water(), _nps(noise=3.5), ArtifactInspectionResult(False))
    table = next(f for f in section if hasattr(f, "_cellvalues"))
    rows = [_texts(row) for row in table._cellvalues]

    assert [re.sub(r"</?b>|<br/>", " ", h).split() for h in rows[0]] == [
        ["Test"], ["Valeur", "mesurée"], ["Référence", "/", "valeur", "attendue"],
        ["Critère", "d'acceptabilité"], ["Conformité"]]
    by_test = {re.sub(r"</?b>", "", row[0]): row[1:] for row in rows[1:]}
    assert list(by_test) == [
        "Nombre CT de l'eau", "Uniformité", "Bruit (stabilité)",
        "Fréquence moyenne du SPB (stabilité)", "Artéfacts (inspection visuelle)"]

    measured, reference, criterion, verdict = by_test["Bruit (stabilité)"]
    low, high = noise_bounds(3.4)  # the bounds printed are the ones the verdict uses
    assert "3,50 UH" in measured and "écart : +0,10 UH" in measured
    assert reference == "3,40 UH"
    assert criterion == (f"{format_fr(low, 2, sign=True)} UH ≤ écart ≤ "
                         f"{format_fr(high, 2, sign=True)} UH")
    assert verdict == "✔ Conforme"

    low, high = nps_bounds(0.31)
    measured, reference, criterion, verdict = by_test["Fréquence moyenne du SPB (stabilité)"]
    assert reference == "0,310 cycles/mm"
    assert f"{format_fr(low, 3)} à {format_fr(high, 3)} cycles/mm" in criterion
    assert by_test["Nombre CT de l'eau"][1] == "0 UH"
    assert "-7 UH ≤ nombre CT ≤ +7 UH" in by_test["Nombre CT de l'eau"][2]


def test_summary_verdicts_use_the_wording_of_the_decision():
    pdf_report._ensure_reportlab()
    inspected = ArtifactInspectionResult(False)

    def verdicts(gen, water, nps, artifacts):
        table = next(f for f in gen._build_summary_section(water, nps, artifacts)
                     if hasattr(f, "_cellvalues"))
        return [_texts(row)[-1] for row in table._cellvalues[1:]]

    with_refs = PDFReportGenerator(reference_noise=3.4, reference_nps_freq=0.31)
    artifacts = ArtifactInspectionResult(True)
    assert verdicts(with_refs, _water(ct=30.0), _nps(noise=4.5), artifacts) == [
        "✘ Non-conformité grave", "✔ Conforme", "✘ Non conforme", "✔ Conforme", "✘ Non conforme"]
    exactly_25 = verdicts(with_refs, _water(ct=25.0), _nps(), inspected)
    assert exactly_25[0] == f"✘ {NC_OR_NCG_NOTICE}"
    # A test that was not judged says why, and never "Conforme"
    assert verdicts(PDFReportGenerator(), _water(), _nps(), None) == [
        "✔ Conforme", "✔ Conforme", "Non évalué — référence absente",
        "Non évalué — référence absente", "Non réalisé"]
    assert verdicts(with_refs, None, None, inspected) == [
        "Non réalisé", "Non réalisé", "Non réalisé", "Non réalisé", "✔ Conforme"]


def test_section_verdicts_carry_a_symbol_and_the_required_action():
    pdf_report._ensure_reportlab()
    gen = PDFReportGenerator(reference_noise=3.4, reference_nps_freq=0.31)
    assert "Résultat : ✔ CONFORME" in _texts(gen._build_ct_number_section(_water()))
    noise = _texts(gen._build_noise_section(_water(), _nps(noise=4.5)))
    assert "Résultat : ✘ NON CONFORME" in noise
    assert "<i>→ Remise en conformité dès que possible</i>" in noise
    grave = _texts(gen._build_ct_number_section(_water(ct=30.0)))
    assert "Résultat : ✘ NON-CONFORMITÉ GRAVE" in grave
    # Orange of the non-conformities: dark enough to be read on white
    assert gen.styles['ResultNC'].textColor.hexval() == "0xb45309"


# --- U-38: slices and ROI labels ---------------------------------------------

def test_slices_used_are_printed(control):
    gen = PDFReportGenerator()
    hu = pdf_report._hu_slice_text(control.image, 1, 4)
    nps = pdf_report._nps_slice_text(control.nps, (0, 3))
    assert hu == "2 / 4 (z = +12,5 mm)"
    assert nps == "1 à 4 (4 coupes, z = +10,0 à +17,5 mm)"
    # Without the indexes the positions are still given
    assert pdf_report._hu_slice_text(control.image, None, None) == "z = +12,5 mm"
    assert pdf_report._nps_slice_text(control.nps, None) == "4 coupes, z = +10,0 à +17,5 mm"

    acquisition = " | ".join(_texts(gen._build_acquisition_section(control.image, hu, nps)))
    assert "Coupe UH : | 2 / 4 (z = +12,5 mm)" in acquisition
    assert "Coupes SPB : | 1 à 4 (4 coupes, z = +10,0 à +17,5 mm)" in acquisition
    rois = " | ".join(_texts(gen._build_roi_visualization_section(
        control.image, control.water, control.nps, None, hu, nps)))
    assert "coupe 2 / 4 (z = +12,5 mm)" in rois and "coupes 1 à 4 (4 coupes" in rois


def test_roi_images_are_labelled_like_the_roi_table(control, monkeypatch):
    labels = []
    monkeypatch.setattr(pdf_report, "_roi_label",
                        lambda ax, x, y, text, color, **kw: labels.append((text, color)))
    for png in (pdf_report._generate_hu_roi_image(control.image, control.water),
                pdf_report._generate_nps_roi_image(control.image, control.nps)):
        assert png.getvalue().startswith(b"\x89PNG")

    # Same colours as on screen: central yellow, peripheral cyan, SPB green
    assert dict(labels[:5]) == {"C": "#ffff00", "12h": "#00ffff", "3h": "#00ffff",
                                "6h": "#00ffff", "9h": "#00ffff"}
    assert labels[5:] == [(str(i), "#00ff00") for i in range(1, 9)]

    table = " | ".join(_texts(PDFReportGenerator()._build_roi_positions_table(
        control.image, control.water, control.nps)))
    for label in ("UH C (centre)", "UH 12h (haut)", "UH 3h (droite)", "UH 6h (bas)",
                  "UH 9h (gauche)", "SPB 1 (haut-gauche)", "SPB 8 (droite)"):
        assert label in table, label


# --- U-39: control type and validation block ----------------------------------

def test_control_type_in_filename():
    assert generate_report_filename("SIEMENS Edge", "666", date="20260315") == (
        "CQI-trimestriel_SIEMENS-Edge_666_2026-03-15.pdf")
    names = [generate_report_filename("Edge", "666", date="20260315", control_type=t)
             for t in CONTROL_TYPES]
    assert names == [
        "CQI-trimestriel_Edge_666_2026-03-15.pdf",
        "CQI-semestriel-per-opératoire_Edge_666_2026-03-15.pdf",
        "CQI-avant-mise-en-service_Edge_666_2026-03-15.pdf",
        "CQI-après-intervention-ou-évolution-logicielle_Edge_666_2026-03-15.pdf",
    ]
    assert generate_report_filename("Edge", "666", date="20260315", control_type="") == (
        "CQI_Edge_666_2026-03-15.pdf")
    free_text = generate_report_filename("Edge", "666", date="20260315",
                                         control_type="suite à panne : tube")
    assert free_text == "CQI-suite-à-panne-tube_Edge_666_2026-03-15.pdf"


def test_control_type_and_names_are_printed(tmp_path, control, story):
    generate_pdf_report(tmp_path / "r.pdf", control.image, control.water, control.nps, None,
                        control_type="semestriel (per-opératoire)",
                        performed_by="A. Martin <MERM>", validated_by="")
    text = " | ".join(_texts(story))
    assert "Contrôle de qualité interne semestriel (per-opératoire) du 15/03/2026" in text
    assert "contrôle réalisé par A. Martin &lt;MERM&gt;" in text
    # Final block: names when given, room to write and sign otherwise
    block = _texts(story[-1:])
    assert block == [
        "Validation",
        "Réalisé par :", "A. Martin &lt;MERM&gt;", "Date :", "", "Signature :",
        "Validé par (physicien médical) :", "", "Date :", "", "Signature :"]


def test_default_title_is_the_quarterly_control(tmp_path, control, story):
    generate_pdf_report(tmp_path / "r.pdf", control.image, control.water)
    assert "<b>Contrôle de qualité interne trimestriel du 15/03/2026</b>" in _texts(story)


def test_new_report_inputs_are_optional_keywords():
    """The existing GUI call keeps working: every new input has a default."""
    new = ("control_type", "performed_by", "validated_by",
           "hu_slice_index", "nps_slice_range", "total_slices")
    parameters = inspect.signature(generate_pdf_report).parameters
    for name in new:
        assert parameters[name].default in ("trimestriel", "", None), name
    filename_parameters = inspect.signature(generate_report_filename).parameters
    assert filename_parameters["control_type"].default == "trimestriel"


# --- U-40: notes, charts --------------------------------------------------------

def test_notes_lists_are_paragraphs_with_rendered_markup(tmp_path, control):
    gen = PDFReportGenerator(notes=NOTES)
    section = gen._build_notes_section()
    assert not any(hasattr(f, "_cellvalues") for f in section)  # no raw-string table any more
    texts = _texts(section)
    assert "<b>À surveiller</b> : bruit proche de la limite" in texts
    # User text is escaped, so "<air>" or "&" cannot break (or inject) markup
    assert "Tube remplacé le <i>12/02/2026</i> &amp; étalonnage &lt;air&gt;" in texts
    assert "Points d'attention" in texts

    out = tmp_path / "notes.pdf"
    generate_pdf_report(out, control.image, control.water,
                        notes=NOTES + "\nTexte <b>non fermé & brut")
    assert out.stat().st_size > 10_000


def test_history_shows_ten_runs_and_full_width_charts(control):
    runs = [_run(series_uid=str(i), run_date=f"2025-{i:02d}-01") for i in range(1, 13)]
    runs[5].noise = 4.5  # out of tolerance
    runs[5].corrective_action = "Étalonnage <air> & eau"
    gen = PDFReportGenerator(reference_noise=3.4, reference_nps_freq=0.31, history=runs)
    section = gen._build_history_section()

    table = next(f for f in section if hasattr(f, "_cellvalues"))
    assert len(table._cellvalues) == 11  # header + the 10 most recent
    texts = _texts(section)
    assert any("12 contrôles enregistrés pour cette installation (10 plus récents affichés)" in t
               for t in texts)
    assert "4,50 UH" in texts and "✘ NC" in texts and "✔ Conforme" in texts
    assert any(t.endswith("— Étalonnage &lt;air&gt; &amp; eau") for t in texts)

    charts = [g for f in section if hasattr(f, "_content")
              for g in f._content if hasattr(g, "drawWidth")]
    assert len(charts) == 2
    assert all(c.drawWidth == pytest.approx(16 * pdf_report.cm) for c in charts)


def test_trend_chart_accepts_another_y_label():
    from cq_tdm.core.trend_chart import render_trend_chart

    runs = [_run(series_uid=str(i), run_date=f"2025-{i:02d}-01") for i in range(1, 5)]
    default = render_trend_chart(runs, "noise", 3.4, 0.31, width_px=400, height_px=200)
    unlabelled = render_trend_chart(runs, "noise", 3.4, 0.31, width_px=400, height_px=200,
                                    ylabel="")
    assert len(default.hits) == len(unlabelled.hits) == 4
    # Without its Y label the plot area starts further left
    assert unlabelled.hits[0][0] < default.hits[0][0]


# --- C-30: free text in cells ---------------------------------------------------

def test_free_text_wraps_in_its_cell(tmp_path, control, story):
    hospital = "Centre Hospitalier Universitaire de Clermont-Ferrand — Site Gabriel-Montpied, " * 2
    algorithm = "iDose4 niveau 3 <hybride> & filtre B, reconstruction itérative, matrice 512 " * 2
    description = "Anneau concentrique de faible contraste à 4 cm du centre <coupes 10 à 14> " * 3
    gen = PDFReportGenerator(hospital_name=hospital, reconstruction_algorithm=algorithm)

    tables = [f for f in gen._build_equipment_section(control.image) if hasattr(f, "_cellvalues")]
    for table in tables:
        for row in table._cellvalues:
            for value, width in zip(row[1::2], table._colWidths[1::2]):
                assert hasattr(value, "wrap"), row[0]  # a Paragraph, not a raw string
                assert value.wrap(width, 10_000)[0] <= width
    cells = {row[0]: row[1] for table in tables for row in table._cellvalues}
    wrapped = cells["Établissement\N{NO-BREAK SPACE}:"]
    # Two lines or more
    assert wrapped.wrap(11.8 * pdf_report.cm, 10_000)[1] >= 2 * wrapped.style.leading

    artifacts = _texts(gen._build_artifact_section(
        control.image, ArtifactInspectionResult(True, description)))
    assert any("&lt;coupes 10 à 14&gt;" in t for t in artifacts)

    # The whole report builds with these values, markup characters included
    out = tmp_path / "long.pdf"
    generate_pdf_report(out, control.image, control.water, control.nps,
                        ArtifactInspectionResult(True, description),
                        hospital_name=hospital, hospital_location=hospital, device_name=hospital,
                        inventory_number="INV <1> & 2", reconstruction_algorithm=algorithm,
                        clinical_protocol_origin=algorithm,
                        dicom_folder="/archives/<cq> & co/" + "x" * 150)
    assert out.stat().st_size > 10_000


def test_header_text_is_cut_to_its_room():
    pdf_report._ensure_reportlab()
    room = 5 * pdf_report.cm
    cut = pdf_report._fit_text("Centre Hospitalier Universitaire " * 5, "Helvetica", 8, room)
    assert cut.endswith("…")
    assert pdf_report.pdfmetrics.stringWidth(cut, "Helvetica", 8) <= room
    assert pdf_report._fit_text("CHU", "Helvetica", 8, room) == "CHU"


def test_portrait_logo_is_capped(tmp_path, control, story):
    from PIL import Image as PILImage

    logo = tmp_path / "logo.png"
    PILImage.new("RGB", (100, 400), "navy").save(logo)
    generate_pdf_report(tmp_path / "r.pdf", control.image, control.water, logo_path=str(logo))
    assert story[0].drawHeight == pytest.approx(6 * pdf_report.cm)
    assert story[0].drawWidth == pytest.approx(1.5 * pdf_report.cm)


# --- C-24: figures are always closed ---------------------------------------------

def test_figures_are_closed_when_drawing_fails(control, monkeypatch):
    import matplotlib.pyplot as plt

    from cq_tdm.core import trend_chart

    def boom(*args, **kwargs):
        raise RuntimeError("rendu impossible")

    before = plt.get_fignums()
    monkeypatch.setattr(pdf_report, "_figure_png", boom)
    for generate, args in (
        (pdf_report._generate_hu_roi_image, (control.image, control.water)),
        (pdf_report._generate_nps_roi_image, (control.image, control.nps)),
        (pdf_report._generate_artifact_image, (control.image,)),
        (pdf_report._generate_nps_plot, (control.nps,)),
    ):
        with pytest.raises(RuntimeError):
            generate(*args)
        assert plt.get_fignums() == before, generate.__name__

    monkeypatch.setattr(trend_chart, "_draw_trend_chart", boom)
    with pytest.raises(RuntimeError):
        trend_chart.render_trend_chart([_run()], "noise", 3.4, 0.31)
    assert plt.get_fignums() == before


# --- U-10: UH, not HU ----------------------------------------------------------------

def test_report_says_uh_not_hu(tmp_path, control, story, monkeypatch):
    figure_texts = []
    figure_png = pdf_report._figure_png

    def recording_png(fig):
        from matplotlib.text import Text
        figure_texts.extend(t.get_text() for t in fig.findobj(Text))
        return figure_png(fig)

    monkeypatch.setattr(pdf_report, "_figure_png", recording_png)
    generate_pdf_report(
        tmp_path / "r.pdf", control.image, control.water, control.nps,
        ArtifactInspectionResult(False),
        reference_noise=control.nps.noise, reference_nps_freq=control.nps.mean_frequency,
        history=[_run(series_uid="A"), _run(series_uid="B", run_date="2026-01-10")],
        hu_slice_index=1, nps_slice_range=(0, 3), total_slices=4)

    printed = _texts(story) + figure_texts
    assert any("UH" in t for t in printed)
    assert "SPB (UH²·mm²)" in figure_texts
    assert [t for t in printed if re.search(r"\bHU\b", t)] == []


# --- control date (C-12) ------------------------------------------------------

def _undated(control):
    import dataclasses
    return dataclasses.replace(control.image, acquisition_date="", study_date="")


def test_date_typed_by_hand_is_printed_as_such(tmp_path, control, story, footers):
    generate_pdf_report(tmp_path / "r.pdf", _undated(control), control.water,
                        control_date="2026-09-15", control_date_manual=True)
    texts = _texts(story)
    assert "<b>Contrôle de qualité interne trimestriel du 15/09/2026</b>" in texts
    assert any("15/09/2026 (saisie manuellement : absente des images DICOM)" in t for t in texts)
    assert all("contrôle du 15/09/2026" in f for f in footers)


def test_images_without_date_are_never_dated_today(tmp_path, control, story, footers):
    from datetime import date

    generate_pdf_report(tmp_path / "r.pdf", _undated(control), control.water)
    texts = _texts(story)
    assert "<b>Contrôle de qualité interne trimestriel (date inconnue)</b>" in texts
    assert all("date du contrôle inconnue" in f for f in footers)
    today = date.today().strftime("%d/%m/%Y")
    assert not any(f"du {today}" in t for t in texts)  # only "édité le <today>" may show today


def test_filename_of_an_undated_control_does_not_use_today():
    assert generate_report_filename("CT", "INV1", "").endswith("_date-inconnue.pdf")
    assert generate_report_filename("CT", "INV1", "2026-09-15").endswith("_2026-09-15.pdf")
    assert generate_report_filename("CT", "INV1", "20260915").endswith("_2026-09-15.pdf")


# --- phantom not detected (U-08) ----------------------------------------------

def test_phantom_not_detected_is_printed(tmp_path, control, story):
    import dataclasses

    water = dataclasses.replace(control.water, phantom_detected=False)
    generate_pdf_report(tmp_path / "r.pdf", control.image, water, control.nps)
    warnings = [t for t in _texts(story) if "paroi du fantôme n'a pas été détectée" in t]
    assert len(warnings) == 2  # under the verdict and above the ROI images
    assert "sur la coupe UH." in warnings[0] and "plage SPB" not in warnings[0]


def test_no_phantom_warning_when_detected(tmp_path, control, story):
    generate_pdf_report(tmp_path / "r.pdf", control.image, control.water, control.nps)
    assert not any("paroi du fantôme" in t for t in _texts(story))


def test_focal_spot_is_printed_readably():
    assert pdf_report._focal_spots_text("1.200000") == "1,2 mm"
    assert pdf_report._focal_spots_text("0.600000/1.200000") == "0,6 / 1,2 mm"
    assert pdf_report._focal_spots_text("") == "—"
    assert pdf_report._focal_spots_text("LARGE") == "LARGE"
