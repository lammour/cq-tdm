"""QC results history: one ``QCRun`` per recorded control of a device.

Runs are stored inside the device database (``devices.json``) and evaluated
against the ANSM criteria here, so that the GUI, the PDF report and the tests
share one definition of "conforme".
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime

# Tolerance for floating point comparisons at the edge of a criterion
_EPS = 1e-9

# Conformity statuses
OK = "ok"
NC = "nc"
NCG = "ncg"
PENDING = "pending"  # cannot be judged (no reference value, no inspection...)
# Overall status only: nothing is non-conforming but at least one test was not judged
INCOMPLETE = "incomplete"
# Water CT number of exactly ±25 HU: the decision lists that value both in the
# "remise en conformité" tier and in the "non-conformité grave" tier, so the
# software does not choose and sends the user to the text
NC_OR_NCG = "nc_or_ncg"

STATUS_LABEL = {OK: "Conforme", NC: "Non conforme", NCG: "NC grave", PENDING: "—",
                INCOMPLETE: "Incomplet", NC_OR_NCG: "NC ou NCG"}
STATUS_SHORT = {OK: "Conforme", NC: "NC", NCG: "NCG", PENDING: "—", INCOMPLETE: "Incomplet",
                NC_OR_NCG: "NC ou NCG"}

NC_OR_NCG_NOTICE = "NC ou NCG : merci de vous référer au texte ANSM"
NC_OR_NCG_DETAIL = (
    "Nombre CT de l'eau égal à 25 UH en valeur absolue : la décision ANSM (point 9.1.7.3) "
    "classe cette valeur à la fois en non-conformité et en non-conformité grave. "
    "Merci de vous référer au texte de la décision."
)

# Metrics that can be trended, with their display label
METRICS = [
    ("water_ct", "Nombre CT de l'eau (HU)"),
    ("uniformity", "Uniformité (HU)"),
    ("noise", "Bruit σ (HU)"),
    ("nps_freq", "Fréquence moyenne SPB (cycles/mm)"),
]


@dataclass
class QCRun:
    """One recorded QC control.

    ``run_date`` is an ISO date (YYYY-MM-DD) taken from the DICOM study date so
    that the history reflects when the phantom was scanned, not when the report
    was written. Reference values are frozen at recording time so that an old
    control keeps the verdict it had when it was signed.
    """

    run_date: str
    run_id: str = ""
    series_uid: str = ""

    # Acquisition
    kvp: float = 0.0
    mas: float = 0.0
    slice_thickness: float = 0.0
    kernel: str = ""

    # Measurements
    water_ct: float = 0.0
    uniformity: float = 0.0
    noise: float = 0.0
    nps_freq: float | None = None
    artifacts_present: bool | None = None
    artifacts_description: str = ""

    # Reference values in force when the run was recorded
    ref_noise: float | None = None
    ref_nps_freq: float | None = None

    # Slices used
    hu_slice_index: int | None = None
    nps_start_slice: int | None = None
    nps_end_slice: int | None = None
    # ROI sizes and offsets used (core.roi_geometry.ROIGeometry.to_dict())
    roi_geometry: dict | None = None
    # Corrective action after a non-conformity (ANSM register: date of the
    # actions taken to restore conformity); date is ISO
    corrective_action_date: str = ""
    corrective_action: str = ""

    pdf_path: str = ""
    # Folder the series was analysed from, so the report can be rebuilt from the
    # images: absolute, and relative to devices.json (see core.dicom_locator)
    dicom_folder: str = ""
    dicom_folder_rel: str = ""
    notes: str = ""
    software_version: str = ""
    recorded_at: str = ""  # ISO datetime of the export

    # Not persisted: True for the measurement on screen that is not recorded yet
    is_current: bool = field(default=False, compare=False)

    def __post_init__(self):
        if not self.run_id:
            self.run_id = self.series_uid or f"{self.run_date}-{uuid.uuid4().hex[:8]}"

    @property
    def date(self) -> date:
        """The run date as a ``date``; falls back to today if the string is malformed."""
        try:
            return date.fromisoformat(self.run_date)
        except ValueError:
            return date.today()

    def date_fr(self) -> str:
        return self.date.strftime("%d/%m/%Y")

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("is_current", None)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "QCRun":
        """Build a run from its stored form; ValueError if it is not a usable run."""
        if not isinstance(data, dict) or not isinstance(data.get("run_date"), str):
            raise ValueError("contrôle sans date (run_date)")
        known = {f.name for f in fields(cls)} - {"is_current"}
        return cls(**{k: v for k, v in data.items() if k in known})


def iso_to_fr(date_iso: str) -> str:
    """ISO date to JJ/MM/AAAA; the input unchanged when it is not a date."""
    try:
        return date.fromisoformat(date_iso[:10]).strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return date_iso or ""


def dicom_date_to_iso(dicom_date: str) -> str:
    """Convert a DICOM DA value (YYYYMMDD) to ISO; today's date if unusable."""
    try:
        return datetime.strptime(dicom_date.strip()[:8], "%Y%m%d").date().isoformat()
    except (ValueError, AttributeError):
        return date.today().isoformat()


# ---------------------------------------------------------------------------
# ANSM criteria (Décision du 18/12/2025, test 9.1.7)
# ---------------------------------------------------------------------------

def noise_bounds(ref_noise: float) -> tuple[float, float]:
    """Allowed deviation from the reference noise: MIN(-0.2, -0.1·σref) ≤ Δ ≤ MAX(0.2, 0.1·σref)."""
    return min(-0.2, -0.1 * ref_noise), max(0.2, 0.1 * ref_noise)


def nps_bounds(ref_nps: float) -> tuple[float, float]:
    """Allowed NPS mean frequency: ±10 % around the reference."""
    return 0.9 * ref_nps, 1.1 * ref_nps


def water_ct_status(value: float) -> str:
    # The decision lists 7 and 25 HU in both neighbouring tiers. At 7 its closing
    # sentence ("inférieure ou égale à ±7 UH") settles it: conforme. At 25 nothing
    # does, so that exact value gets a status of its own.
    v = abs(value)
    if v <= 7:
        return OK
    if abs(v - 25) <= _EPS:
        return NC_OR_NCG
    return NC if v < 25 else NCG


def uniformity_status(value: float) -> str:
    return OK if abs(value) <= 7 else NC


def noise_status(value: float, ref_noise: float | None) -> str:
    if ref_noise is None:
        return PENDING
    lo, hi = noise_bounds(ref_noise)
    return OK if lo - _EPS <= value - ref_noise <= hi + _EPS else NC


def nps_status(value: float | None, ref_nps: float | None) -> str:
    if value is None or not ref_nps:
        return PENDING
    lo, hi = nps_bounds(ref_nps)
    return OK if lo - _EPS <= value <= hi + _EPS else NC


def artifacts_status(present: bool | None) -> str:
    if present is None:
        return PENDING
    return NC if present else OK


def overall_status(statuses: list[str]) -> str:
    """Verdict of a whole control.

    A non-conformity stands whatever else is missing. Without one, a control is
    only "conforme" when every test was judged: a test left pending (artifacts
    not inspected, no reference value, no SPB) makes it incomplete.
    """
    judged = [s for s in statuses if s != PENDING]
    for worst in (NCG, NC_OR_NCG, NC):
        if worst in judged:
            return worst
    if not judged:
        return PENDING
    return OK if len(judged) == len(statuses) else INCOMPLETE


# What is missing when a test is pending, as printed on the report and asked
# before an export
PENDING_REASON = {
    "water_ct": "nombre CT de l'eau non mesuré",
    "uniformity": "uniformité non mesurée",
    "noise": "stabilité du bruit non évaluée (valeur de référence absente)",
    "nps_freq": "stabilité du SPB non évaluée (valeur de référence absente)",
    "artifacts": "inspection visuelle des artéfacts non réalisée",
}


def evaluate_measurements(
    water_ct: float | None,
    uniformity: float | None,
    noise: float | None,
    nps_freq: float | None,
    artifacts_present: bool | None,
    ref_noise: float | None,
    ref_nps_freq: float | None,
) -> dict[str, str]:
    """Status of each test plus ``overall``; a value of None is a test not done."""
    s = {
        "water_ct": PENDING if water_ct is None else water_ct_status(water_ct),
        "uniformity": PENDING if uniformity is None else uniformity_status(uniformity),
        "noise": PENDING if noise is None else noise_status(noise, ref_noise),
        "nps_freq": nps_status(nps_freq, ref_nps_freq),
        "artifacts": artifacts_status(artifacts_present),
    }
    s["overall"] = overall_status(list(s.values()))
    return s


def pending_reasons(statuses: dict[str, str], nps_measured: bool = True) -> list[str]:
    """What keeps a control from being complete, one phrase per pending test."""
    reasons = []
    for key, text in PENDING_REASON.items():
        if statuses.get(key) != PENDING:
            continue
        if key == "nps_freq" and not nps_measured:
            text = "SPB non mesuré"
        reasons.append(text)
    return reasons


def evaluate_run(run: QCRun) -> dict[str, str]:
    """Return the status of each test plus ``overall``."""
    return evaluate_measurements(
        run.water_ct, run.uniformity, run.noise, run.nps_freq, run.artifacts_present,
        run.ref_noise, run.ref_nps_freq)


def tolerance_band(metric: str, ref_noise: float | None, ref_nps: float | None):
    """(low, high, reference) for a metric, or None when no band applies.

    ``reference`` is the value to draw as the reference line, or None.
    """
    if metric == "water_ct":
        return -7.0, 7.0, None
    if metric == "uniformity":
        return 0.0, 7.0, None
    if metric == "noise" and ref_noise is not None:
        lo, hi = noise_bounds(ref_noise)
        return ref_noise + lo, ref_noise + hi, ref_noise
    if metric == "nps_freq" and ref_nps:
        lo, hi = nps_bounds(ref_nps)
        return lo, hi, ref_nps
    return None
