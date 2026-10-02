"""Main application window."""

import base64
import html
import logging
import time
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QEventLoop, QTimer, QRegularExpression, QDate
from PySide6.QtGui import QShortcut, QKeySequence, QFont, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QScrollArea,
    QDateEdit,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QStatusBar,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QSplitter,
    QTextBrowser,
    QComboBox,
    QMessageBox,
    QDialog,
    QListWidget,
    QListWidgetItem,
    QFormLayout,
    QSpinBox,
    QTabWidget,
    QCheckBox,
)

# Lightweight imports - no heavy dependencies
from ..core.app_config import get_app_config, save_app_config
from ..core.device_database import DeviceConfig, DeviceDatabase
from ..core.utils import format_fr, parse_float_fr
from ..core.qc_history import (
    CONTROL_TYPES, DEFAULT_CONTROL_TYPE, INCOMPLETE, NC_OR_NCG, NC_OR_NCG_NOTICE, NCG, OK,
    UNKNOWN_DATE, QCRun, dicom_date_to_iso,
    evaluate_measurements, iso_to_fr, noise_bounds, noise_status, nps_status, pending_reasons,
    water_ct_status,
)
from ..core.dicom_locator import (
    find_series_folder, folder_matches, relative_to_database, relocation_between, resolve_dicom_folder,
)
from .history_panel import HistoryPanel
from .messages import ask, ask_save, french_button_box

# Heavy imports - deferred via lazy __init__.py (pydicom, numpy, scipy)
from ..core import (
    DicomImage,
    DicomSeries,
    load_dicom_folder,
    calculate_rois,
    analyze_water_phantom,
    WaterPhantomResults,
    analyze_nps,
    NPSResult,
    detect_phantom,
    ROIGeometry,
)

# Report imports - deferred via lazy __init__.py (reportlab)
from ..reports import generate_pdf_report, generate_report_filename, ArtifactInspectionResult
from .theme import theme_colors, primary_button_style


logger = logging.getLogger(__name__)

# Selector entry and button labels for the "no installation selected" states
NO_INSTALL_LABEL = "Aucune installation sélectionnée"   # no series loaded yet
UNKNOWN_INSTALL_LABEL = "Installation inconnue"         # series loaded, not recognised
NEW_INSTALL_LABEL = "Nouvelle installation"
EDIT_INSTALL_LABEL = "Modifier…"

# Register of operations items (ANSM decision, point 3.2.2) only the operator
# knows: (DeviceConfig attribute, label, placeholder)
REGISTER_FIELDS = (
    ("phantom_brand", "Fantôme, marque :", "ex. Pro-Project, PTW…"),
    ("phantom_model", "Fantôme, modèle :", "ex. fantôme d'eau 20 cm"),
    ("phantom_serial", "Fantôme, n° de série :", ""),
    ("clinical_protocol_origin", "Protocole clinique d'origine :",
     "Protocole clinique dont le protocole de CQ est issu"),
    ("reconstruction_algorithm", "Algorithme de reconstruction :",
     "Algorithme et niveau du protocole de CQ, ex. iDose niveau 3"),
)


# The theme palette lives in gui/theme.py so the image viewer shares it
_theme_colors = theme_colors


from .image_viewer import ImageViewerWidget, ROI, ArtifactInspectionDialog


def warn_database_load(parent, db: DeviceDatabase) -> None:
    """Tell the user what could not be read from the installations database, if anything."""
    if db.read_only and db.load_error:
        # The folder itself is missing: typically a network share not mounted
        QMessageBox.warning(
            parent,
            "Base de données des installations",
            f"Le dossier de la base de données des installations est inaccessible :\n"
            f"{db.db_path.parent}\n\n"
            "Vérifiez que le partage réseau est connecté. En attendant, aucune installation "
            "n'est affichée et rien ne peut être enregistré : la base sera relue dès qu'elle "
            "sera de nouveau accessible.",
        )
    elif db.read_only:
        QMessageBox.warning(
            parent,
            "Base de données des installations",
            "Cette base de données a été écrite par une version plus récente de CQ TDM.\n\n"
            "Les installations et leurs contrôles sont affichés, mais rien ne peut être "
            "enregistré depuis ce poste tant que CQ TDM n'y est pas mis à jour.",
        )
    elif db.load_error:
        QMessageBox.warning(
            parent,
            "Base de données des installations",
            "Le fichier de la base de données des installations n'a pas pu être lu.\n"
            "Une copie datée (extension .bak) a été enregistrée à côté de lui et "
            "l'application utilise une base vide.\n\n"
            f"Détail : {db.load_error}",
        )
    elif db.load_warnings:
        QMessageBox.warning(
            parent,
            "Base de données des installations",
            "Certaines entrées de la base de données des installations n'ont pas pu être "
            "lues et sont ignorées :\n\n"
            + "\n".join(f"• {w}" for w in db.load_warnings)
            + f"\n\nElles sont conservées telles quelles dans le fichier {db.db_path} ; "
            "le reste de la base est utilisable.",
        )


def reference_question(
    installation: str,
    old_noise: float | None,
    old_nps_freq: float | None,
    noise: float,
    nps_freq: float | None,
    source: str,
    freezes_geometry: bool,
) -> tuple[str, str]:
    """(text, label of the accept button) of the question asked before defining references.

    Reference values decide every later verdict of stability: the question
    shows what is replaced, so that an established reference is not
    overwritten unnoticed.
    """
    replacing = old_noise is not None or old_nps_freq is not None

    def change(old: float | None, new: float, decimals: int, unit: str) -> str:
        if not replacing:
            return f"{format_fr(new, decimals)} {unit}"
        before = format_fr(old, decimals) if old is not None else "non définie"
        return f"{before} → {format_fr(new, decimals)} {unit}"

    lines = [f"σ : {change(old_noise, noise, 2, 'UH')}"]
    if nps_freq is not None:
        lines.append(f"f SPB : {change(old_nps_freq, nps_freq, 3, 'c/mm')}")
    elif old_nps_freq is not None:
        lines.append(f"f SPB : {format_fr(old_nps_freq, 3)} c/mm (inchangée, SPB non mesuré)")

    verb = "Remplacer" if replacing else "Définir"
    text = (f"{verb} les valeurs de référence de « {installation} » ({source}) ?\n\n"
            + "\n".join(lines)
            + "\n\nLes prochains contrôles seront jugés par rapport à ces valeurs.")
    if freezes_geometry:
        text += (" Les tailles et positions des ROI de ce contrôle seront figées et "
                 "réutilisées à l'identique.")
    return text, "Remplacer" if replacing else "Définir comme références"


class DeviceManagerDialog(QDialog):
    """The one window where installations are created, edited and deleted.

    Opened from "Modifier…" next to the installation selector and from the
    Configuration menu. It does not need an image: coordinates, reference
    values and register items are edited for any saved installation. When an
    image is loaded, the installation matching it can be created from its
    DICOM identity, and the slices chosen in the viewer can be recorded.
    """

    def __init__(
        self,
        device_db: DeviceDatabase,
        parent=None,
        select_device_id: str | None = None,
        current_image: DicomImage | None = None,
        current_slices: tuple[int, int, int] | None = None,
        current_series_length: int | None = None,
        current_analysis: tuple[float, float | None, ROIGeometry | None] | None = None,
        image_device_id: str | None = None,
    ):
        super().__init__(parent)
        self.device_db = device_db
        self.current_image = current_image
        # Id of the installation the loaded image belongs to (saved or not).
        # It derives from the DICOM identity; an image without any identity
        # (anonymised series) belongs to the installation the caller attached
        # it to, if any.
        if image_device_id is None and current_image is not None:
            image_device_id = DeviceConfig.generate_id(
                current_image.manufacturer or "", current_image.model_name or "",
                current_image.station_name or "", current_image.device_serial_number or "") or None
        self.image_device_id = image_device_id
        self.current_slices = current_slices  # (hu, nps_start, nps_end), 0-based
        # Number of slices of the loaded series: saved with the slices, which
        # only apply to a series of that length
        self.current_series_length = current_series_length
        # (noise, SPB mean frequency or None, ROI geometry or None) of the
        # analysis on screen, offered as reference values
        self.current_analysis = current_analysis
        self.setWindowTitle("Gestion des installations")
        self.setMinimumSize(860, 600)
        self.resize(960, 780)
        self._setup_ui()
        self._refresh_device_list(select_device_id)

    @property
    def selected_device_id(self) -> str | None:
        """Id of the installation selected in the list, None if none."""
        return self._current_device.device_id if self._current_device else None

    def _image_device_id(self) -> str | None:
        """Id of the installation of the loaded image (saved or not); None when unknown."""
        return self.image_device_id

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QHBoxLayout(self)

        # Left panel - device list
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Database file section (at top)
        db_label = QLabel("Base de données :")
        db_label.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(db_label)

        self._db_path_label = QLabel()
        self._db_path_label.setWordWrap(True)
        self._db_path_label.setStyleSheet("color: #666; font-size: 11px;")
        self._update_db_path_label()
        left_layout.addWidget(self._db_path_label)

        db_buttons = QWidget()
        db_buttons_layout = QHBoxLayout(db_buttons)
        db_buttons_layout.setContentsMargins(0, 0, 0, 0)

        btn_select_db = QPushButton("Ouvrir…")
        btn_select_db.clicked.connect(self._select_database)
        db_buttons_layout.addWidget(btn_select_db)

        btn_new_db = QPushButton("Nouvelle…")
        btn_new_db.clicked.connect(self._create_new_database)
        db_buttons_layout.addWidget(btn_new_db)

        btn_move_db = QPushButton("Déplacer…")
        btn_move_db.clicked.connect(self._move_database)
        db_buttons_layout.addWidget(btn_move_db)

        left_layout.addWidget(db_buttons)

        left_layout.addSpacing(15)

        # Device list
        list_label = QLabel("Installations enregistrées :")
        list_label.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(list_label)

        self._device_list = QListWidget()
        self._device_list.currentItemChanged.connect(self._on_device_selected)
        left_layout.addWidget(self._device_list)

        # New installation from the loaded image (its DICOM identity is the id)
        self._btn_new_from_image = QPushButton("Nouvelle installation depuis l'image chargée")
        self._btn_new_from_image.clicked.connect(self._create_from_image)
        left_layout.addWidget(self._btn_new_from_image)

        # Delete button
        self._btn_delete = QPushButton("Supprimer l'installation")
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._delete_selected_device)
        left_layout.addWidget(self._btn_delete)

        layout.addWidget(left_panel, 2)

        # Right panel - device details (editable)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        details_label = QLabel("Détails de l'installation :")
        details_label.setStyleSheet("font-weight: bold;")
        right_layout.addWidget(details_label)

        # Form for device details
        form = QWidget()
        form_layout = QFormLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)

        self._edit_hospital = QLineEdit()
        self._edit_hospital.setPlaceholderText("Nom de l'établissement")
        form_layout.addRow("Établissement :", self._edit_hospital)

        self._edit_location = QLineEdit()
        self._edit_location.setPlaceholderText("Localisation de l'installation")
        form_layout.addRow("Localisation :", self._edit_location)

        self._edit_device = QLineEdit()
        self._edit_device.setPlaceholderText("Nom de l'installation")
        form_layout.addRow("Installation :", self._edit_device)

        self._edit_commissioning = QLineEdit()
        self._edit_commissioning.setPlaceholderText("Date de mise en service")
        form_layout.addRow("Mise en service :", self._edit_commissioning)

        self._edit_serial_number = QLineEdit()
        self._edit_serial_number.setPlaceholderText("Numéro de série")
        form_layout.addRow("N° série :", self._edit_serial_number)

        self._edit_inventory = QLineEdit()
        self._edit_inventory.setPlaceholderText("Numéro d'inventaire")
        form_layout.addRow("N° inventaire :", self._edit_inventory)

        # Reference values section
        form_layout.addRow(QLabel(""))  # Spacer
        ref_label = QLabel("Valeurs de référence :")
        ref_label.setStyleSheet("font-weight: bold;")
        form_layout.addRow(ref_label)

        # Digits and one decimal separator (dot or comma) only: a locale-aware
        # QDoubleValidator rewrites "4,52" as "452" outside a French locale
        ref_validator = QRegularExpressionValidator(QRegularExpression(r"\d*[.,]?\d*"))
        self._edit_ref_noise = QLineEdit()
        self._edit_ref_noise.setPlaceholderText("Bruit de référence (UH)")
        self._edit_ref_noise.setValidator(ref_validator)
        form_layout.addRow("Bruit réf. (σ) :", self._edit_ref_noise)

        self._edit_ref_nps_freq = QLineEdit()
        self._edit_ref_nps_freq.setPlaceholderText("Fréquence moyenne SPB (cycles/mm)")
        self._edit_ref_nps_freq.setValidator(ref_validator)
        form_layout.addRow("Fréq. SPB réf. :", self._edit_ref_nps_freq)

        # First control: the measured values are what later controls compare to
        self._btn_ref_from_analysis = QPushButton("Définir les valeurs actuelles comme références")
        self._btn_ref_from_analysis.clicked.connect(self._set_references_from_analysis)
        form_layout.addRow("", self._btn_ref_from_analysis)

        # Register of operations (ANSM 3.2.2): printed on every report
        form_layout.addRow(QLabel(""))  # Spacer
        register_label = QLabel("Registre des opérations :")
        register_label.setStyleSheet("font-weight: bold;")
        register_label.setToolTip(
            "Informations exigées dans le registre des opérations (décision ANSM, point 3.2.2), "
            "reprises dans chaque rapport")
        form_layout.addRow(register_label)
        self._edit_register: dict[str, QLineEdit] = {}
        for attr, label, placeholder in REGISTER_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            form_layout.addRow(label, edit)
            self._edit_register[attr] = edit

        # DICOM identification (read-only)
        form_layout.addRow(QLabel(""))  # Spacer
        dicom_label = QLabel("Identification DICOM :")
        dicom_label.setStyleSheet("font-weight: bold; color: #888;")
        form_layout.addRow(dicom_label)

        self._label_manufacturer = QLabel("—")
        self._label_manufacturer.setStyleSheet("color: #888;")
        form_layout.addRow("Fabricant :", self._label_manufacturer)

        self._label_model = QLabel("—")
        self._label_model.setStyleSheet("color: #888;")
        form_layout.addRow("Modèle :", self._label_model)

        self._label_station = QLabel("—")
        self._label_station.setStyleSheet("color: #888;")
        form_layout.addRow("Station :", self._label_station)

        self._label_dicom_serial = QLabel("—")
        self._label_dicom_serial.setStyleSheet("color: #888;")
        form_layout.addRow("N° série DICOM :", self._label_dicom_serial)

        self._label_runs = QLabel("—")
        self._label_runs.setStyleSheet("color: #888;")
        form_layout.addRow("Contrôles enregistrés :", self._label_runs)

        # Saved slice positions (read-only)
        form_layout.addRow(QLabel(""))  # Spacer
        slices_label = QLabel("Positions des coupes :")
        slices_label.setStyleSheet("font-weight: bold; color: #888;")
        form_layout.addRow(slices_label)

        self._label_hu_slice = QLabel("—")
        self._label_hu_slice.setStyleSheet("color: #888;")
        form_layout.addRow("Coupe UH :", self._label_hu_slice)

        self._label_nps_range = QLabel("—")
        self._label_nps_range.setStyleSheet("color: #888;")
        form_layout.addRow("Plage SPB :", self._label_nps_range)

        # The slices are chosen in the viewer; record the current choice here
        self._btn_adopt_slices = QPushButton("Mémoriser les coupes actuelles")
        self._btn_adopt_slices.clicked.connect(self._adopt_current_slices)
        form_layout.addRow("", self._btn_adopt_slices)

        # Frozen ROI geometry (read-only): sizes and positions every control reuses
        form_layout.addRow(QLabel(""))  # Spacer
        geometry_label = QLabel("Géométrie des ROI :")
        geometry_label.setStyleSheet("font-weight: bold; color: #888;")
        geometry_label.setToolTip(
            "Tailles et positions des ROI, figées lors du contrôle de référence et réutilisées "
            "à l'identique aux contrôles suivants (seul le centre du fantôme est redétecté)")
        form_layout.addRow(geometry_label)
        self._labels_geometry: dict[str, QLabel] = {}
        for key, title in (("state", "État :"), ("format", "Format d'image :"),
                           ("central", "ROI centrale :"), ("peripheral", "ROI périphériques :"),
                           ("nps", "ROI SPB :")):
            label = QLabel("—")
            label.setStyleSheet("color: #888;")
            label.setWordWrap(True)
            form_layout.addRow(title, label)
            self._labels_geometry[key] = label
        self._btn_reset_geometry = QPushButton("Réinitialiser la géométrie des ROI")
        self._btn_reset_geometry.setToolTip(
            "Oublier les tailles et positions figées, par exemple après un changement de protocole "
            "(matrice, champ de vue) : elles seront recalculées puis figées à nouveau lors de la "
            "prochaine définition des valeurs de référence ou du prochain contrôle enregistré")
        self._btn_reset_geometry.clicked.connect(self._reset_roi_geometry)
        form_layout.addRow("", self._btn_reset_geometry)

        # The form can outgrow a small screen: scroll it vertically only, the
        # fields follow the width of the window
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(form)
        right_layout.addWidget(scroll, 1)

        # Save and close buttons
        buttons_row = QHBoxLayout()
        self._btn_save = QPushButton("Enregistrer les modifications")
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._save_current_device)
        buttons_row.addWidget(self._btn_save, 1)
        btn_close = QPushButton("Fermer")
        btn_close.setAutoDefault(False)
        btn_close.clicked.connect(self.reject)
        buttons_row.addWidget(btn_close)
        right_layout.addLayout(buttons_row)
        # Content of the form when it was last loaded or saved (see _is_dirty)
        self._loaded_form: tuple = ()
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        right_layout.addWidget(self._status_label)

        layout.addWidget(right_panel, 3)

        # Store current device
        self._current_device: DeviceConfig | None = None

    def _refresh_device_list(self, select_device_id: str | None = None):
        """Rebuild the device list, selecting `select_device_id` when given."""
        image_id = self._image_device_id()
        self._device_list.blockSignals(True)
        self._device_list.clear()
        devices = self.device_db.get_all_devices()
        selected_row = -1
        for row, device in enumerate(devices):
            label = device.display_name()
            if device.device_id == image_id:
                label += "   (image chargée)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, device.device_id)
            self._device_list.addItem(item)
            if device.device_id == select_device_id:
                selected_row = row
        self._device_list.blockSignals(False)
        if selected_row >= 0:
            self._device_list.setCurrentRow(selected_row)
            self._on_device_selected(self._device_list.currentItem(), None)
        else:
            self._current_device = None
            self._clear_form()
        # Creating the image's installation is the thing to do when it is missing
        can_create = self.current_image is not None and (
            image_id is None or self.device_db.get_device(image_id) is None)
        self._btn_new_from_image.setEnabled(can_create)
        self._btn_new_from_image.setStyleSheet(primary_button_style() if can_create else "")
        if self.current_image is None:
            tooltip = "Chargez une série DICOM pour créer l'installation de son scanner"
        elif image_id is None:
            tooltip = ("Créer une installation pour la série chargée. Ses images n'identifient "
                       "pas le scanner (série anonymisée) : l'installation devra être "
                       "sélectionnée à la main à chaque contrôle")
        else:
            tooltip = ("Créer l'installation du scanner dont une série est chargée "
                       "(identifiée par fabricant, modèle, station et n° de série DICOM)")
        self._btn_new_from_image.setToolTip(tooltip)

    def _form_values(self) -> tuple:
        """What is typed in the form, to tell whether it differs from what was loaded."""
        edits = [self._edit_hospital, self._edit_location, self._edit_device,
                 self._edit_commissioning, self._edit_serial_number, self._edit_inventory,
                 self._edit_ref_noise, self._edit_ref_nps_freq, *self._edit_register.values()]
        return tuple(edit.text() for edit in edits)

    def _is_dirty(self) -> bool:
        """True when the form holds changes that were not saved."""
        return self._current_device is not None and self._form_values() != self._loaded_form

    def _confirm_pending_edits(self) -> bool:
        """Before leaving the installation being edited: save, drop or stay.

        Returns False when the user chooses to stay (or the save fails), in
        which case the caller must not move on.
        """
        if not self._is_dirty():
            return True
        device = self._current_device
        choice = ask_save(
            self, "Modifications non enregistrées",
            f"Les modifications de « {device.display_name()} » ne sont pas enregistrées.")
        if choice == "cancel":
            return False
        if choice == "save":
            self._apply_form_to_device()
            try:
                self.device_db.save_device(device)
            except OSError as e:
                QMessageBox.warning(self, "Enregistrement",
                                    f"Impossible d'enregistrer l'installation :\n{e}")
                return False
            self._remember_database_path()
            self._relabel_device(device)
        self._loaded_form = self._form_values()  # nothing pending any more
        return True

    def _relabel_device(self, device: DeviceConfig):
        """Refresh the list entry of `device` after its name changed."""
        for row in range(self._device_list.count()):
            item = self._device_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == device.device_id:
                label = device.display_name()
                if device.device_id == self._image_device_id():
                    label += "   (image chargée)"
                item.setText(label)

    def reject(self):
        """Close the window (button, Escape or the title bar cross), unless edits must be kept."""
        if self._confirm_pending_edits():
            super().reject()

    def _on_device_selected(self, current: QListWidgetItem, previous: QListWidgetItem):
        """Handle device selection."""
        if previous is not None and current is not previous and not self._confirm_pending_edits():
            # Stay on the installation being edited
            self._device_list.blockSignals(True)
            self._device_list.setCurrentItem(previous)
            self._device_list.blockSignals(False)
            return
        if current is None:
            self._current_device = None
            self._clear_form()
            self._btn_delete.setEnabled(False)
            self._btn_save.setEnabled(False)
            return

        device_id = current.data(Qt.ItemDataRole.UserRole)
        device = self.device_db.get_device(device_id)

        if device:
            self._current_device = device
            self._load_device_to_form(device)
            self._btn_delete.setEnabled(True)
            self._btn_save.setEnabled(True)

    def _load_device_to_form(self, device: DeviceConfig):
        """Load device data into the form."""
        self._status_label.setText("")
        for attr, edit in self._edit_register.items():
            edit.setText(getattr(device, attr, "") or "")
        # The analysis on screen can only become the reference of its own scanner
        can_reference = self.current_analysis is not None and device.device_id == self._image_device_id()
        self._btn_ref_from_analysis.setEnabled(can_reference)
        if can_reference:
            noise, nps_freq, _geometry = self.current_analysis
            values = f"σ {format_fr(noise, 2)} UH"
            if nps_freq is not None:
                values += f" · f SPB {format_fr(nps_freq, 3)} c/mm"
            self._btn_ref_from_analysis.setToolTip(
                f"Reprendre les valeurs de l'analyse en cours ({values}) comme valeurs de référence "
                "de cette installation et figer la géométrie des ROI de ce contrôle")
            # No reference yet: this is the thing to do
            no_reference = device.reference_noise is None and device.reference_nps_freq is None
            self._btn_ref_from_analysis.setStyleSheet(primary_button_style() if no_reference else "")
        else:
            self._btn_ref_from_analysis.setStyleSheet("")
            self._btn_ref_from_analysis.setToolTip(
                "Chargez et analysez une série de cette installation pour reprendre ses valeurs")
        # Slices can only be recorded for the installation the loaded series belongs to
        can_adopt = self.current_slices is not None and device.device_id == self._image_device_id()
        self._btn_adopt_slices.setEnabled(can_adopt)
        if can_adopt:
            hu, start, end = self.current_slices
            self._btn_adopt_slices.setText(
                f"Mémoriser les coupes actuelles (UH {hu + 1} · SPB {start + 1}–{end + 1})")
            self._btn_adopt_slices.setToolTip(
                "Remplacer les coupes enregistrées par celles choisies dans la visionneuse")
        else:
            self._btn_adopt_slices.setText("Mémoriser les coupes actuelles")
            self._btn_adopt_slices.setToolTip(
                "Chargez une série de cette installation et choisissez les coupes dans la visionneuse")
        self._edit_hospital.setText(device.hospital_name)
        self._edit_location.setText(device.hospital_location)
        self._edit_device.setText(device.device_name)
        self._edit_commissioning.setText(device.commissioning_date)
        self._edit_serial_number.setText(device.serial_number)
        self._edit_inventory.setText(device.inventory_number)

        # Reference values
        if device.reference_noise is not None:
            self._edit_ref_noise.setText(format_fr(device.reference_noise, 2))
        else:
            self._edit_ref_noise.clear()
        if device.reference_nps_freq is not None:
            self._edit_ref_nps_freq.setText(format_fr(device.reference_nps_freq, 3))
        else:
            self._edit_ref_nps_freq.clear()

        self._label_manufacturer.setText(device.dicom_manufacturer or "—")
        self._label_model.setText(device.dicom_model_name or "—")
        self._label_station.setText(device.dicom_station_name or "—")
        self._label_dicom_serial.setText(device.dicom_serial_number or "—")
        if device.runs:
            last = max(device.runs, key=lambda r: (r.run_date, r.recorded_at))
            self._label_runs.setText(f"{len(device.runs)} (dernier le {last.date_fr()})")
        else:
            self._label_runs.setText("aucun")
        self._show_geometry(device.roi_geometry)

        # Slice positions (displayed as 1-based for user)
        if device.hu_slice_index is not None:
            on = (f" (série de {device.slices_series_length} coupes)"
                  if device.slices_series_length else "")
            self._label_hu_slice.setText(f"{device.hu_slice_index + 1}{on}")
        else:
            self._label_hu_slice.setText("—")
        if device.nps_start_slice is not None and device.nps_end_slice is not None:
            self._label_nps_range.setText(f"{device.nps_start_slice + 1} - {device.nps_end_slice + 1}")
        else:
            self._label_nps_range.setText("—")
        self._loaded_form = self._form_values()

    def _clear_form(self):
        """Clear the form fields."""
        self._edit_hospital.clear()
        self._edit_location.clear()
        self._edit_device.clear()
        self._edit_commissioning.clear()
        self._edit_serial_number.clear()
        self._edit_inventory.clear()
        self._edit_ref_noise.clear()
        self._edit_ref_nps_freq.clear()
        for edit in self._edit_register.values():
            edit.clear()
        self._label_manufacturer.setText("—")
        self._label_model.setText("—")
        self._label_station.setText("—")
        self._label_dicom_serial.setText("—")
        self._label_runs.setText("—")
        self._show_geometry(None, no_device=True)
        self._label_hu_slice.setText("—")
        self._label_nps_range.setText("—")
        self._btn_adopt_slices.setEnabled(False)
        self._btn_adopt_slices.setText("Mémoriser les coupes actuelles")
        self._btn_ref_from_analysis.setEnabled(False)
        self._btn_ref_from_analysis.setStyleSheet("")
        self._btn_delete.setEnabled(False)
        self._btn_save.setEnabled(False)
        self._status_label.setText("")
        self._loaded_form = self._form_values()

    def _save_current_device(self):
        """Save modifications to the current device."""
        if self._current_device is None:
            return
        self._apply_form_to_device()
        self._persist(self._current_device, "Modifications enregistrées")

    def _apply_form_to_device(self):
        """Copy the form into the selected device (not saved yet)."""
        # Update device with form values
        self._current_device.hospital_name = self._edit_hospital.text()
        self._current_device.hospital_location = self._edit_location.text()
        self._current_device.device_name = self._edit_device.text()
        self._current_device.commissioning_date = self._edit_commissioning.text()
        self._current_device.serial_number = self._edit_serial_number.text()
        self._current_device.inventory_number = self._edit_inventory.text()

        # Update reference values (accept both dot and comma as decimal separator)
        ref_noise_text = self._edit_ref_noise.text().strip()
        self._current_device.reference_noise = parse_float_fr(ref_noise_text) if ref_noise_text else None

        ref_freq_text = self._edit_ref_nps_freq.text().strip()
        self._current_device.reference_nps_freq = parse_float_fr(ref_freq_text) if ref_freq_text else None

        for attr, edit in self._edit_register.items():
            setattr(self._current_device, attr, edit.text().strip())

    def _persist(self, device: DeviceConfig, message: str):
        """Save `device`, rebuild the list on it and report in the status line."""
        try:
            self.device_db.save_device(device)
        except OSError as e:
            QMessageBox.warning(self, "Enregistrement", f"Impossible d'enregistrer l'installation :\n{e}")
            return
        self._remember_database_path()
        self._update_db_path_label()  # the first save creates the file
        self._refresh_device_list(device.device_id)
        from datetime import datetime
        self._status_label.setText(f"{message} — {device.display_name()}, {datetime.now():%H:%M}")

    def _remember_database_path(self) -> bool:
        """Record the open database as the one to open next time; False if it could not be saved.

        The default location is not written down: the settings then follow the
        user profile if it is moved to another account or workstation.
        """
        config = get_app_config()
        is_default = self.device_db.db_path == DeviceDatabase.default_path()
        wanted = "" if is_default else str(self.device_db.db_path)
        if config.device_database_path == wanted:
            return True
        config.device_database_path = wanted
        try:
            save_app_config()
        except OSError as e:
            QMessageBox.warning(
                self, "Base de données des installations",
                "Le choix de cette base n'a pas pu être enregistré dans les réglages : "
                f"elle devra être rouverte au prochain lancement.\n\n{e}")
            return False
        return True

    def _create_from_image(self):
        """Create and save the installation of the loaded image's scanner."""
        img = self.current_image
        if img is None:
            return
        image_id = self._image_device_id()
        if image_id is not None and self.device_db.get_device(image_id) is not None:
            self._refresh_device_list(image_id)
            return
        device = DeviceConfig.from_dicom(img.manufacturer or "", img.model_name or "",
                                         img.station_name or "", img.device_serial_number or "")
        if not device.device_id:
            # No DICOM identity: a unique id, or every anonymised series would
            # share one installation
            device.device_id = DeviceConfig.new_manual_id()
        self.image_device_id = device.device_id
        device.device_name = f"{img.manufacturer or ''} {img.model_name or ''}".strip()
        if self.current_slices is not None:
            device.hu_slice_index, device.nps_start_slice, device.nps_end_slice = self.current_slices
            device.slices_series_length = self.current_series_length
        self._persist(device, "Installation créée : complétez ses informations puis enregistrez")
        self._edit_hospital.setFocus()

    def _show_geometry(self, geometry: ROIGeometry | None, no_device: bool = False):
        """Fill the read-only "Géométrie des ROI" section."""
        labels = self._labels_geometry
        self._btn_reset_geometry.setEnabled(geometry is not None)
        if geometry is None:
            labels["state"].setText(
                "—" if no_device else
                "non figée (le sera à la définition des valeurs de référence "
                "ou au premier contrôle enregistré)")
            for key in ("format", "central", "peripheral", "nps"):
                labels[key].setText("—")
            return
        px = geometry.pixel_size_mm

        def mm(pixels: float) -> str:
            return format_fr(pixels * px, 1)

        labels["state"].setText(
            f"figée depuis le contrôle du {iso_to_fr(geometry.frozen_date)}"
            if geometry.is_frozen else "calculée, non datée")
        labels["format"].setText(
            f"matrice {geometry.columns} × {geometry.rows} · pixel {format_fr(px, 3)} mm · "
            f"fantôme Ø {mm(geometry.phantom_diameter_px)} mm (intérieur)")
        labels["central"].setText(
            f"Ø {2 * geometry.central_radius} px ({mm(2 * geometry.central_radius)} mm)")
        wall_gap = geometry.phantom_diameter_px / 2 - geometry.peripheral_distance - geometry.peripheral_radius
        labels["peripheral"].setText(
            f"Ø {2 * geometry.peripheral_radius} px ({mm(2 * geometry.peripheral_radius)} mm) · "
            f"centre à {geometry.peripheral_distance} px du centre du fantôme · "
            f"bord externe à {mm(wall_gap)} mm de la paroi interne")
        labels["nps"].setText(
            f"{geometry.nps_roi_size} × {geometry.nps_roi_size} px ({mm(geometry.nps_roi_size)} mm) · "
            f"centres à {geometry.nps_cardinal_distance} px (cardinales) et "
            f"{geometry.nps_diagonal_offset} px par axe (diagonales) du centre du fantôme")

    def _reset_roi_geometry(self):
        """Forget the frozen ROI geometry of the selected installation."""
        device = self._current_device
        if device is None or device.roi_geometry is None:
            return
        if not ask(
                self, "Géométrie des ROI",
                "Oublier les tailles et positions de ROI figées pour cette installation ?\n\n"
                "Elles seront recalculées sur la prochaine série, puis figées à nouveau lors de la "
                "prochaine définition des valeurs de référence ou du prochain contrôle enregistré. "
                "La fréquence moyenne du SPB dépend de la taille des ROI : redéfinissez les valeurs "
                "de référence si la géométrie change.",
                "Réinitialiser", "Annuler"):
            return
        self._apply_form_to_device()  # keep what was typed in the other fields
        device.roi_geometry = None
        self._persist(device, "Géométrie des ROI réinitialisée")

    def _adopt_current_slices(self):
        """Record the slices chosen in the viewer on the selected installation."""
        if self._current_device is None or self.current_slices is None:
            return
        hu, start, end = self.current_slices
        self._apply_form_to_device()  # keep what was typed in the other fields
        self._current_device.hu_slice_index = hu
        self._current_device.slices_series_length = self.current_series_length
        self._current_device.nps_start_slice = start
        self._current_device.nps_end_slice = end
        self._persist(self._current_device, "Coupes mémorisées")

    def _set_references_from_analysis(self):
        """Make the analysis on screen the reference of the selected installation.

        Same effect as the link of the main window summary: reference values
        and frozen ROI geometry come from the same control.
        """
        if self._current_device is None or self.current_analysis is None:
            return
        noise, nps_freq, geometry = self.current_analysis
        # What is replaced is what the form shows, saved or just typed
        old_noise_text = self._edit_ref_noise.text().strip()
        old_nps_text = self._edit_ref_nps_freq.text().strip()
        text, accept = reference_question(
            self._current_device.display_name(),
            parse_float_fr(old_noise_text) if old_noise_text else None,
            parse_float_fr(old_nps_text) if old_nps_text else None,
            noise, nps_freq, "analyse en cours", geometry is not None)
        if not ask(self, "Valeurs de référence", text, accept, "Annuler"):
            return
        self._apply_form_to_device()  # keep what was typed in the other fields
        self._current_device.reference_noise = noise
        # A missing SPB frequency leaves the recorded one alone
        if nps_freq is not None:
            self._current_device.reference_nps_freq = nps_freq
        if geometry is not None:
            self._current_device.roi_geometry = geometry
        self._persist(self._current_device, "Valeurs de référence définies depuis l'analyse en cours")

    def _delete_selected_device(self):
        """Delete the selected device."""
        if self._current_device is None:
            return

        device = self._current_device
        n = len(device.runs)
        question = f"Supprimer l'installation « {device.display_name()} »"
        if n:
            question += (f" et ses {n} contrôles enregistrés" if n > 1 else " et son contrôle enregistré")
        question += " ?"
        if n:
            question += ("\n\nL'historique de l'installation sera perdu. Les rapports PDF et les "
                         "images DICOM ne sont pas supprimés.")
        if not ask(self, "Supprimer l'installation", question + "\n\nCette action est irréversible.",
                   "Supprimer", "Annuler", destructive=True):
            return
        try:
            self.device_db.delete_device(device.device_id)
        except OSError as e:
            QMessageBox.warning(self, "Supprimer l'installation",
                                f"Impossible de supprimer l'installation :\n{e}")
            return
        self._current_device = None
        self._refresh_device_list()
        self._clear_form()

    def _update_db_path_label(self):
        """Update the database path label."""
        if self.device_db.db_path.exists():
            self._db_path_label.setText(str(self.device_db.db_path))
            self._db_path_label.setEnabled(True)
        else:
            self._db_path_label.setText("Aucune base de données")
            self._db_path_label.setEnabled(False)

    def _select_database(self):
        """Open an existing database file."""
        if not self._confirm_pending_edits():
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Ouvrir une base de données",
            "",
            "Fichiers JSON (*.json);;Tous les fichiers (*)"
        )
        if file_path:
            self._switch_database(file_path)

    def _create_new_database(self):
        """Create a new database file."""
        if not self._confirm_pending_edits():
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Créer une nouvelle base de données",
            "devices.json",
            "Fichiers JSON (*.json)"
        )
        if file_path:
            # Create empty database file
            try:
                Path(file_path).parent.mkdir(parents=True, exist_ok=True)
                Path(file_path).write_text('{"version": 2, "devices": []}', encoding="utf-8")
            except OSError as e:
                QMessageBox.warning(self, "Nouvelle base de données",
                                    f"Impossible de créer la base de données :\n{e}")
                return
            self._switch_database(file_path)

    def _move_database(self):
        """Move the current database to a new location."""
        import shutil

        if not self._confirm_pending_edits():
            return

        # Check if database exists
        if not self.device_db.db_path.exists():
            QMessageBox.warning(
                self,
                "Aucune base de données",
                "Il n'y a pas de base de données à déplacer."
            )
            return

        old_path = self.device_db.db_path

        # Ask for new location
        new_path, _ = QFileDialog.getSaveFileName(
            self,
            "Déplacer la base de données",
            old_path.name,
            "Fichiers JSON (*.json)"
        )

        if not new_path:
            return

        new_path = Path(new_path)

        # Don't allow moving to the same location
        if new_path.resolve() == old_path.resolve():
            QMessageBox.information(
                self,
                "Même emplacement",
                "Le nouvel emplacement est identique à l'emplacement actuel."
            )
            return

        try:
            # Create parent directory if needed
            new_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy the database to new location
            shutil.copy2(old_path, new_path)

            # Switch to the new database
            self._switch_database(str(new_path))

            # Ask if user wants to delete the old file
            if ask(
                    self, "Supprimer l'ancienne base",
                    f"La base de données a été copiée vers :\n{new_path}\n\n"
                    f"Voulez-vous supprimer l'ancienne base de données ?\n{old_path}",
                    "Supprimer l'ancienne base", "La conserver"):
                try:
                    old_path.unlink()
                except Exception as e:
                    QMessageBox.warning(
                        self,
                        "Erreur de suppression",
                        f"Impossible de supprimer l'ancien fichier :\n{e}"
                    )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Déplacer la base de données",
                f"Impossible de déplacer la base de données :\n{e}"
            )

    def _switch_database(self, file_path: str):
        """Switch to a different database file; the current one stays open if it is unreadable."""
        # Probe first: a file of another kind, or written by a newer version,
        # must not replace the database in use, nor be overwritten by a save
        candidate = DeviceDatabase(Path(file_path), backup_unreadable=False)
        if candidate.load_error:
            QMessageBox.critical(
                self, "Base de données des installations",
                "Ce fichier n'est pas une base de données d'installations lisible. "
                f"La base actuelle reste ouverte.\n\nDétail : {candidate.load_error}")
            return
        self.device_db = candidate
        self._remember_database_path()
        warn_database_load(self, self.device_db)
        self._update_db_path_label()
        self._refresh_device_list()
        self._clear_form()


class ImageInfoDialog(QDialog):
    """Dialog for displaying image information."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Informations image")
        self.setMinimumSize(500, 400)
        self._setup_ui()

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        self._info_text = QLabel("Aucune image chargée")
        self._info_text.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._info_text.setWordWrap(True)
        self._info_text.setStyleSheet("font-family: monospace; font-size: 12px;")
        self._info_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._info_text, 1)

        # Close button
        btn_close = QPushButton("Fermer")
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)

    def set_info(self, info_text: str):
        """Set the information text to display."""
        self._info_text.setText(info_text)


class ReportSettingsDialog(QDialog):
    """Dialog for configuring report settings (logo, etc.)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuration des rapports")
        self.setMinimumSize(500, 300)
        self._selected_logo_path = ""
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Logo file selection
        logo_label = QLabel("Logo :")
        logo_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(logo_label)

        file_row = QHBoxLayout()
        self._logo_path_label = QLabel("Aucun logo sélectionné")
        self._logo_path_label.setEnabled(False)
        file_row.addWidget(self._logo_path_label, 1)

        btn_select = QPushButton("Parcourir…")
        btn_select.clicked.connect(self._select_logo)
        file_row.addWidget(btn_select)

        self._btn_clear = QPushButton("Supprimer")
        self._btn_clear.clicked.connect(self._clear_logo)
        self._btn_clear.setEnabled(False)
        file_row.addWidget(self._btn_clear)

        layout.addLayout(file_row)

        # Logo preview
        self._logo_preview = QLabel()
        self._logo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo_preview.setMinimumHeight(120)
        self._logo_preview.setFrameShape(QLabel.Shape.StyledPanel)
        layout.addWidget(self._logo_preview)

        # Scale input
        scale_row = QHBoxLayout()
        scale_label = QLabel("Échelle :")
        scale_row.addWidget(scale_label)

        self._scale_spinbox = QSpinBox()
        self._scale_spinbox.setRange(10, 100)
        self._scale_spinbox.setValue(40)
        self._scale_spinbox.setSuffix(" %")
        self._scale_spinbox.setSingleStep(5)
        scale_row.addWidget(self._scale_spinbox)

        scale_row.addStretch()
        layout.addLayout(scale_row)

        # Content options
        content_label = QLabel("Contenu :")
        content_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(content_label)

        self._check_history = QCheckBox("Inclure l'historique des contrôles")
        self._check_history.setToolTip(
            "Ajoute au rapport une section « Historique des contrôles » : tableau des dix "
            "derniers contrôles enregistrés pour l'installation et courbes de tendance du "
            "bruit et de la fréquence moyenne SPB."
        )
        layout.addWidget(self._check_history)

        layout.addStretch()

        # Buttons
        button_box = french_button_box("Enregistrer", "Annuler")
        button_box.accepted.connect(self._save_and_close)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_settings(self):
        """Load current settings."""
        config = get_app_config()
        if config.report_logo_path and Path(config.report_logo_path).exists():
            self._selected_logo_path = config.report_logo_path
            self._update_logo_display()
        self._scale_spinbox.setValue(int(config.report_logo_scale * 100))
        self._check_history.setChecked(config.report_include_history)

    def _select_logo(self):
        """Select a logo file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner un logo",
            "",
            "Images (*.png *.jpg *.jpeg);;Tous les fichiers (*)"
        )
        if file_path:
            self._selected_logo_path = file_path
            self._update_logo_display()

    def _clear_logo(self):
        """Clear the selected logo."""
        self._selected_logo_path = ""
        self._update_logo_display()

    def _update_logo_display(self):
        """Update the logo display."""
        if self._selected_logo_path and Path(self._selected_logo_path).exists():
            self._logo_path_label.setText(Path(self._selected_logo_path).name)
            self._logo_path_label.setEnabled(True)
            self._btn_clear.setEnabled(True)

            # Show preview
            from PySide6.QtGui import QPixmap
            pixmap = QPixmap(self._selected_logo_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    250, 100,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self._logo_preview.setPixmap(scaled)
            else:
                self._logo_preview.setText("Aperçu non disponible")
        else:
            self._logo_path_label.setText("Aucun logo sélectionné")
            self._logo_path_label.setEnabled(False)
            self._btn_clear.setEnabled(False)
            self._logo_preview.clear()

    def _save_and_close(self):
        """Save settings and close dialog."""
        config = get_app_config()
        config.report_logo_path = self._selected_logo_path
        config.report_logo_scale = self._scale_spinbox.value() / 100.0
        config.report_include_history = self._check_history.isChecked()
        save_app_config()
        self.accept()


class NotesEditorDialog(QDialog):
    """Dialog for editing notes with simplified markdown support."""

    def __init__(self, initial_text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Notes")
        self.setMinimumSize(600, 450)
        self._setup_ui()
        self._text_edit.setPlainText(initial_text)

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # Instructions
        instructions = QLabel(
            "Rédigez vos notes ci-dessous. Formatage markdown simplifié supporté :"
        )
        layout.addWidget(instructions)

        # Formatting hints
        hints = QLabel(
            "<span style='color: #888; font-size: 11px;'>"
            "<b>**texte**</b> = gras  |  "
            "<i>*texte*</i> = italique  |  "
            "<b>## Titre</b> = titre  |  "
            "<b>- item</b> = liste"
            "</span>"
        )
        hints.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(hints)

        # Toolbar with formatting buttons
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 4, 0, 4)
        toolbar_layout.setSpacing(4)

        btn_bold = QPushButton("G")
        btn_bold.setToolTip("Gras (**texte**)")
        btn_bold.setMaximumWidth(30)
        btn_bold.setStyleSheet("font-weight: bold;")
        btn_bold.clicked.connect(self._insert_bold)
        toolbar_layout.addWidget(btn_bold)

        btn_italic = QPushButton("I")
        btn_italic.setToolTip("Italique (*texte*)")
        btn_italic.setMaximumWidth(30)
        btn_italic.setStyleSheet("font-style: italic;")
        btn_italic.clicked.connect(self._insert_italic)
        toolbar_layout.addWidget(btn_italic)

        btn_heading = QPushButton("H")
        btn_heading.setToolTip("Titre (## Titre)")
        btn_heading.setMaximumWidth(30)
        btn_heading.clicked.connect(self._insert_heading)
        toolbar_layout.addWidget(btn_heading)

        btn_list = QPushButton("•")
        btn_list.setToolTip("Liste (- item)")
        btn_list.setMaximumWidth(30)
        btn_list.clicked.connect(self._insert_list)
        toolbar_layout.addWidget(btn_list)

        toolbar_layout.addStretch()
        layout.addWidget(toolbar)

        # Text editor
        from PySide6.QtWidgets import QPlainTextEdit
        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlaceholderText(
            "Saisissez vos notes ici...\n\n"
            "Exemples :\n"
            "## Observations\n"
            "- Point important\n"
            "- **À surveiller** : valeur limite\n"
        )
        c = _theme_colors()
        self._text_edit.setStyleSheet(f"""
            QPlainTextEdit {{
                font-family: monospace;
                font-size: 12px;
                background-color: {c['bg']};
                color: {c['text']};
                border: 1px solid {c['border']};
                border-radius: 4px;
                padding: 8px;
            }}
        """)
        layout.addWidget(self._text_edit, 1)

        # Buttons
        button_box = french_button_box("Valider", "Annuler")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _insert_bold(self):
        """Insert bold markers around selection or at cursor."""
        self._wrap_selection("**", "**")

    def _insert_italic(self):
        """Insert italic markers around selection or at cursor."""
        self._wrap_selection("*", "*")

    def _insert_heading(self):
        """Insert heading marker at start of line."""
        cursor = self._text_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.StartOfLine)
        cursor.insertText("## ")
        self._text_edit.setTextCursor(cursor)

    def _insert_list(self):
        """Insert list marker at start of line."""
        cursor = self._text_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.StartOfLine)
        cursor.insertText("- ")
        self._text_edit.setTextCursor(cursor)

    def _wrap_selection(self, prefix: str, suffix: str):
        """Wrap current selection with prefix and suffix."""
        cursor = self._text_edit.textCursor()
        if cursor.hasSelection():
            text = cursor.selectedText()
            cursor.insertText(f"{prefix}{text}{suffix}")
        else:
            pos = cursor.position()
            cursor.insertText(f"{prefix}{suffix}")
            cursor.setPosition(pos + len(prefix))
            self._text_edit.setTextCursor(cursor)

    def get_text(self) -> str:
        """Get the notes text."""
        return self._text_edit.toPlainText()


class CorrectiveActionDialog(QDialog):
    """Date and nature of the corrective action taken after a control."""

    def __init__(self, run: QCRun, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Action corrective — contrôle du {run.date_fr()}")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        self._done = QCheckBox("Une action corrective a été réalisée")
        self._done.setChecked(bool(run.corrective_action_date or run.corrective_action))
        layout.addWidget(self._done)
        form = QFormLayout()
        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("dd/MM/yyyy")
        existing = QDate.fromString(run.corrective_action_date[:10], "yyyy-MM-dd")
        self._date.setDate(existing if existing.isValid() else QDate.currentDate())
        form.addRow("Date de l'action :", self._date)
        self._text = QLineEdit(run.corrective_action)
        self._text.setPlaceholderText("ex. recalibration par le fabricant, nouveau contrôle conforme")
        form.addRow("Nature :", self._text)
        layout.addLayout(form)
        self._done.toggled.connect(self._date.setEnabled)
        self._done.toggled.connect(self._text.setEnabled)
        self._date.setEnabled(self._done.isChecked())
        self._text.setEnabled(self._done.isChecked())
        buttons = french_button_box("Enregistrer", "Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_to(self, run: QCRun):
        if self._done.isChecked():
            run.corrective_action_date = self._date.date().toString("yyyy-MM-dd")
            run.corrective_action = self._text.text().strip()
        else:
            run.corrective_action_date = ""
            run.corrective_action = ""


class ExportDialog(QDialog):
    """What the report needs and the images do not say.

    The type of control and the names of the validation block; and the date
    of the control when the images carry none, so that it is never taken
    from the day of the export.
    """

    def __init__(self, parent=None, *, control_type: str = DEFAULT_CONTROL_TYPE,
                 performed_by: str = "", validated_by: str = "",
                 ask_date: bool = False, manual_date: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Enregistrer le contrôle")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._date: QDateEdit | None = None
        if ask_date:
            warning = QLabel(
                "Les images de cette série ne contiennent aucune date d'acquisition. "
                "Indiquez la date à laquelle le fantôme a été scanné : elle figurera dans le "
                "rapport et dans l'historique avec la mention « saisie manuellement ».")
            warning.setWordWrap(True)
            layout.addWidget(warning)
            self._date = QDateEdit()
            self._date.setCalendarPopup(True)
            self._date.setDisplayFormat("dd/MM/yyyy")
            previous = QDate.fromString(manual_date, "yyyy-MM-dd")
            self._date.setDate(previous if previous.isValid() else QDate.currentDate())
            self._date.setMaximumDate(QDate.currentDate())
            form.addRow("Date du contrôle :", self._date)

        self._type = QComboBox()
        self._type.setEditable(True)
        self._type.addItems(CONTROL_TYPES)
        self._type.setCurrentText(control_type or DEFAULT_CONTROL_TYPE)
        self._type.setToolTip("Type de contrôle interne, repris dans le titre et le nom du rapport ; "
                              "un autre libellé peut être saisi")
        form.addRow("Type de contrôle :", self._type)

        self._performed_by = QLineEdit(performed_by)
        self._performed_by.setPlaceholderText("Nom de la personne qui a réalisé le contrôle")
        form.addRow("Réalisé par :", self._performed_by)
        self._validated_by = QLineEdit(validated_by)
        self._validated_by.setPlaceholderText("Physicien médical (laisser vide pour compléter à la main)")
        form.addRow("Validé par :", self._validated_by)
        layout.addLayout(form)

        buttons = french_button_box("Continuer", "Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def control_type(self) -> str:
        return self._type.currentText().strip()

    @property
    def performed_by(self) -> str:
        return self._performed_by.text().strip()

    @property
    def validated_by(self) -> str:
        return self._validated_by.text().strip()

    @property
    def manual_date(self) -> str:
        """ISO date typed by the user, "" when the date was not asked."""
        return self._date.date().toString("yyyy-MM-dd") if self._date is not None else ""


class MainWindow(QMainWindow):
    """Main application window for CQ TDM."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CQ TDM")
        self.setMinimumSize(900, 600)
        self.setAcceptDrops(True)

        self._current_image: DicomImage | None = None
        self._current_series: DicomSeries | None = None
        self._current_results: WaterPhantomResults | None = None
        self._nps_results: NPSResult | None = None
        self._artifact_result: bool | None = None  # True=present (NC), False=absent (Conforme)
        self._artifact_description: str = ""  # Description of artifacts if present
        self._user_notes: str = ""  # User notes for the PDF report
        self._current_folder: str = ""  # Folder the current series was loaded from
        # ISO date of the control typed by the user when the images carry none
        self._manual_control_date: str = ""
        # Type of control and names of the validation block, asked at export
        self._control_type: str = ""
        self._performed_by: str = get_app_config().last_performed_by
        self._validated_by: str = get_app_config().last_validated_by
        self._debug_mode: bool = False  # Debug mode for phantom detection visualization
        # True when the HU analysis on screen could not use the frozen ROI geometry
        self._hu_geometry_mismatch: bool = False

        # Device database and current device (use custom path from config if set)
        config = get_app_config()
        db_path = Path(config.device_database_path) if config.device_database_path else None
        self._device_db = DeviceDatabase(db_path)
        self._current_device: DeviceConfig | None = None
        if self._device_db.load_error or self._device_db.load_warnings or self._device_db.read_only:
            QTimer.singleShot(0, lambda: warn_database_load(self, self._device_db))
        # When the database file was last checked for changes made elsewhere
        self._database_checked_at: float = time.monotonic()

        # Report metadata (empty by default, shown as grey placeholders in UI)
        self._hospital_name: str = ""
        self._hospital_location: str = ""
        self._device_name: str = ""
        self._commissioning_date: str = ""
        self._serial_number: str = ""
        self._inventory_number: str = ""

        # Debounce timer for NPS range changes (1 second)
        self._nps_debounce_timer = QTimer()
        self._nps_debounce_timer.setSingleShot(True)
        self._nps_debounce_timer.setInterval(1000)  # 1 second
        self._nps_debounce_timer.timeout.connect(self._run_debounced_nps_analysis)
        self._pending_nps_range: tuple[int, int] | None = None

        # Debounce timer for HU slice changes (300ms - faster for responsiveness)
        self._hu_debounce_timer = QTimer()
        self._hu_debounce_timer.setSingleShot(True)
        self._hu_debounce_timer.setInterval(300)  # 300ms
        self._hu_debounce_timer.timeout.connect(self._run_debounced_hu_analysis)
        self._pending_hu_slice: int | None = None

        # Debounce timer for reference value changes (500ms)
        self._ref_debounce_timer = QTimer()
        self._ref_debounce_timer.setSingleShot(True)
        self._ref_debounce_timer.setInterval(500)  # 500ms
        self._ref_debounce_timer.timeout.connect(self._run_debounced_reference_update)

        # Saved slice values for tracking modifications
        self._saved_hu_slice: int | None = None
        self._saved_nps_start: int | None = None
        self._saved_nps_end: int | None = None
        # (length the saved slices were chosen on, length of the loaded series)
        # when they differ: the saved slices are then not applied
        self._saved_slices_mismatch: tuple[int, int] | None = None

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._setup_shortcuts()

        # No image yet: the history of the installation used last is the home view
        self._select_device(self._default_device())
        self._results_tabs.setCurrentIndex(self._results_tabs.indexOf(self.history_panel))

    def _setup_menu(self):
        """Set up the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&Fichier")
        file_menu.addAction("Ouvrir un dossier DICOM…", self._open_dicom_folder, "Ctrl+O")
        file_menu.addSeparator()
        file_menu.addAction("Enregistrer le contrôle et exporter le PDF…", self._export_pdf, "Ctrl+E")
        file_menu.addAction("Exporter les positions des ROI SPB (JSON)…", self._export_nps_rois_json)
        file_menu.addAction("Exporter les positions des ROI UH (JSON)…", self._export_hu_rois_json)
        file_menu.addSeparator()
        file_menu.addAction("Quitter", self.close, "Ctrl+Q")

        # View menu
        view_menu = menubar.addMenu("&Affichage")
        view_menu.addAction("Informations sur l'image…", self._show_image_info, "Ctrl+I")
        # Shows what the phantom detection found: the first thing to look at
        # when the ROIs are not where they should be
        self._debug_action = view_menu.addAction("Afficher le contour détecté du fantôme")
        self._debug_action.setCheckable(True)
        self._debug_action.setChecked(False)
        self._debug_action.triggered.connect(self._toggle_debug_mode)
        view_menu.addSeparator()
        self._theme_action = view_menu.addAction("Thème clair")
        self._theme_action.setCheckable(True)
        self._theme_action.setChecked(get_app_config().theme == "light")
        self._theme_action.triggered.connect(self._toggle_theme)

        # Configuration menu
        config_menu = menubar.addMenu("&Configuration")
        config_menu.addAction("Gestion des installations…", self._show_device_manager)
        config_menu.addAction("Rapports…", self._show_report_settings)

        # Help menu
        help_menu = menubar.addMenu("&Aide")
        help_menu.addAction("Aide", self._show_help, "F1")
        help_menu.addAction("Raccourcis clavier", self._show_shortcuts)
        help_menu.addAction("Ouvrir le dossier du journal", self._open_log_folder)
        help_menu.addSeparator()
        help_menu.addAction("À propos", self._show_about)

    def _setup_ui(self):
        """Set up the main UI layout."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QHBoxLayout(central_widget)

        # Main splitter for resizable panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # Left panel - Image viewer
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Image viewer (includes slice slider)
        self.image_viewer = ImageViewerWidget()
        self.image_viewer.slice_changed.connect(self._on_slice_changed)
        self.image_viewer.hu_slice_changed.connect(self._on_hu_slice_changed)
        self.image_viewer.nps_range_changed.connect(self._on_nps_range_changed)
        self.image_viewer.open_folder_requested.connect(self._open_dicom_folder)

        # Slice selection (HU slice, SPB range) sits right under the slider whose
        # markers it drives, with the reset-to-saved button at the far right
        slice_row = QWidget()
        slice_row_layout = QHBoxLayout(slice_row)
        slice_row_layout.setContentsMargins(5, 0, 5, 2)
        slice_row_layout.addWidget(self.image_viewer.slice_controls_widget)
        slice_row_layout.addStretch(1)
        self._btn_reset_slices = QPushButton("⟲ Réinit. coupes")
        self._btn_reset_slices.setToolTip("Revenir aux coupes enregistrées pour cette installation")
        self._btn_reset_slices.setEnabled(False)
        self._btn_reset_slices.clicked.connect(self._reset_slices_to_saved)
        slice_row_layout.addWidget(self._btn_reset_slices)
        self.image_viewer.layout().insertWidget(1, slice_row)  # index 0 is the slider bar

        left_layout.addWidget(self.image_viewer, 1)

        # Right panel - Results and controls
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # Installation info section (editable)
        device_label = QLabel("Installation")
        device_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(device_label)

        # Installation selector dropdown
        device_selector_layout = QHBoxLayout()
        device_selector_layout.setContentsMargins(0, 0, 0, 0)
        self._device_combo = QComboBox()
        self._device_combo.addItem(NO_INSTALL_LABEL, None)
        self._refresh_device_combo()
        self._device_combo.currentIndexChanged.connect(self._on_device_selected)
        device_selector_layout.addWidget(self._device_combo, 1)

        # The installation form lives in a dialog, opened from here
        self._btn_edit_device = QPushButton(EDIT_INSTALL_LABEL)
        self._btn_edit_device.clicked.connect(self._edit_installation)
        self._update_install_selector()
        device_selector_layout.addWidget(self._btn_edit_device)

        right_layout.addLayout(device_selector_layout)

        self._install_summary = QLabel()
        self._install_summary.setWordWrap(True)
        self._install_summary.setOpenExternalLinks(False)
        self._install_summary.linkActivated.connect(self._on_install_summary_link)
        self._install_summary.setStyleSheet("color: #888; font-size: 11px; margin-left: 2px;")
        right_layout.addWidget(self._install_summary)

        # Installation fields. They are not shown here: the installation is
        # edited in the "Gestion des installations" window. They stay as the
        # in-memory mirror of the current device that the results view, the
        # PDF export and the history read, refreshed by _load_device_config.
        self._edit_hospital_name = QLineEdit()
        self._edit_hospital_location = QLineEdit()
        self._edit_device_name = QLineEdit()
        self._edit_commissioning_date = QLineEdit()
        self._edit_serial_number = QLineEdit()
        self._edit_inventory_number = QLineEdit()
        self._edit_ref_noise = QLineEdit()
        self._edit_ref_nps_freq = QLineEdit()
        for edit in (self._edit_hospital_name, self._edit_hospital_location, self._edit_device_name,
                     self._edit_commissioning_date, self._edit_serial_number, self._edit_inventory_number,
                     self._edit_ref_noise, self._edit_ref_nps_freq):
            edit.textChanged.connect(self._on_field_changed)
        # Reference fields drive the debounced comparison in the results view
        self._edit_ref_noise.textChanged.connect(self._on_reference_field_changed)
        self._edit_ref_nps_freq.textChanged.connect(self._on_reference_field_changed)

        self._update_install_summary()
        right_layout.addSpacing(10)

        # Slice selection controls (from image viewer)
        # Results section
        results_label = QLabel("Résultats d'analyse")
        results_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(results_label)

        self.results_browser = QTextBrowser()
        self.results_browser.setOpenExternalLinks(False)
        c = _theme_colors()
        self.results_browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {c['bg']};
                color: {c['text']};
                border: 1px solid {c['border']};
                border-radius: 4px;
                font-size: 12px;
            }}
        """)
        self.results_browser.setHtml(self._get_empty_results_html())

        # Results and history side by side in tabs
        self.history_panel = HistoryPanel()
        self.history_panel.reference_requested.connect(self._apply_reference_from_history)
        self.history_panel.delete_requested.connect(self._delete_history_run)
        self.history_panel.pdf_relinked.connect(self._relink_history_pdf)
        self.history_panel.load_series_requested.connect(self._load_run_series)
        self.history_panel.corrective_action_requested.connect(self._edit_corrective_action)
        self._results_tabs = QTabWidget()
        self._results_tabs.addTab(self.results_browser, "Résultats")
        self._results_tabs.addTab(self.history_panel, "Historique")
        right_layout.addWidget(self._results_tabs, 1)

        # Artifact inspection button
        self.btn_artifact = QPushButton("Inspection visuelle des artéfacts")
        self.btn_artifact.setEnabled(False)
        self.btn_artifact.clicked.connect(self._inspect_artifacts)
        right_layout.addWidget(self.btn_artifact)

        # Notes button
        self.btn_notes = QPushButton("Ajouter une observation")
        self.btn_notes.clicked.connect(self._edit_notes)
        right_layout.addWidget(self.btn_notes)

        # Export button
        self.btn_export = QPushButton("Enregistrer le contrôle et exporter le PDF")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export_pdf)
        right_layout.addWidget(self.btn_export)

        # Add panels to splitter
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([700, 500])

    def _setup_statusbar(self):
        """Set up the status bar."""
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self.statusbar.showMessage("Prêt")
        # What was loaded stays readable at the right while messages come and go
        self._series_status = QLabel()
        self.statusbar.addPermanentWidget(self._series_status)

    def _setup_shortcuts(self):
        """Set up keyboard shortcuts."""
        # Slice navigation
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self._prev_slice)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._next_slice)
        QShortcut(QKeySequence(Qt.Key.Key_Home), self, self._first_slice)
        QShortcut(QKeySequence(Qt.Key.Key_End), self, self._last_slice)
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, self._prev_10_slices)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, self._next_10_slices)

        # Go to HU slice position
        QShortcut(QKeySequence("H"), self, self._go_to_hu_slice)

        # Go to center of NPS range
        QShortcut(QKeySequence("N"), self, self._go_to_nps_center)

        # View controls
        QShortcut(QKeySequence("F"), self, self._fit_image_to_view)
        QShortcut(QKeySequence("R"), self, self._reset_view)
        QShortcut(QKeySequence("U"), self, self._toggle_water_rois)
        QShortcut(QKeySequence("S"), self, self._toggle_nps_rois)
        QShortcut(QKeySequence("I"), self, self._toggle_info_overlays)

        # Analysis
        QShortcut(QKeySequence("A"), self, self._inspect_artifacts)

    def _prev_slice(self):
        """Go to previous slice."""
        if self._current_series and self._current_series.num_images > 1:
            current = self.image_viewer.slice_slider.value()
            if current > 0:
                self.image_viewer.slice_slider.setValue(current - 1)

    def _next_slice(self):
        """Go to next slice."""
        if self._current_series and self._current_series.num_images > 1:
            current = self.image_viewer.slice_slider.value()
            if current < self._current_series.num_images - 1:
                self.image_viewer.slice_slider.setValue(current + 1)

    def _first_slice(self):
        """Go to first slice."""
        if self._current_series and self._current_series.num_images > 1:
            self.image_viewer.slice_slider.setValue(0)

    def _last_slice(self):
        """Go to last slice."""
        if self._current_series and self._current_series.num_images > 1:
            self.image_viewer.slice_slider.setValue(self._current_series.num_images - 1)

    def _prev_10_slices(self):
        """Go back 10 slices."""
        if self._current_series and self._current_series.num_images > 1:
            current = self.image_viewer.slice_slider.value()
            self.image_viewer.slice_slider.setValue(max(0, current - 10))

    def _next_10_slices(self):
        """Go forward 10 slices."""
        if self._current_series and self._current_series.num_images > 1:
            current = self.image_viewer.slice_slider.value()
            self.image_viewer.slice_slider.setValue(min(self._current_series.num_images - 1, current + 10))

    def _go_to_hu_slice(self):
        """Go to the HU analysis slice."""
        if self._current_series:
            hu_slice = self.image_viewer.get_hu_slice_index()
            self.image_viewer.slice_slider.setValue(hu_slice)

    def _go_to_nps_center(self):
        """Go to the center of the NPS range."""
        if self._current_series:
            start, end = self.image_viewer.get_nps_slice_range()
            center = (start + end) // 2
            self.image_viewer.slice_slider.setValue(center)

    def _fit_image_to_view(self):
        """Fit the image to the viewer (reset zoom)."""
        self.image_viewer._reset_zoom()

    def _reset_view(self):
        """Reset the entire view (slice, zoom, W/L, analysis slices)."""
        self.image_viewer._reset_all()

    def _toggle_water_rois(self):
        """Toggle water phantom ROI visibility."""
        self.image_viewer._toggle_water_rois()

    def _toggle_nps_rois(self):
        """Toggle NPS ROI visibility."""
        self.image_viewer._toggle_nps_rois()

    def _toggle_info_overlays(self):
        """Toggle the image information and folder path shown over the image."""
        self.image_viewer._toggle_info_overlays()

    def _show_shortcuts(self):
        """Show keyboard shortcuts dialog."""
        shortcuts = """
<h3>Navigation des coupes</h3>
<table>
<tr><td width="120"><b>←</b> / <b>→</b></td><td>Coupe précédente / suivante</td></tr>
<tr><td><b>Page préc.</b> / <b>Page suiv.</b></td><td>Sauter 10 coupes</td></tr>
<tr><td><b>Début</b> / <b>Fin</b></td><td>Première / dernière coupe</td></tr>
<tr><td><b>H</b></td><td>Aller à la coupe UH</td></tr>
<tr><td><b>N</b></td><td>Aller au centre de la plage SPB</td></tr>
</table>

<h3>Affichage</h3>
<table>
<tr><td width="120"><b>F</b></td><td>Ajuster l'image à la vue</td></tr>
<tr><td><b>R</b></td><td>Réinitialiser l'affichage et remettre les coupes d'analyse au centre de la série</td></tr>
<tr><td><b>U</b></td><td>Afficher/masquer les ROI UH (jaune et cyan)</td></tr>
<tr><td><b>S</b></td><td>Afficher/masquer les ROI SPB (vert)</td></tr>
<tr><td><b>I</b></td><td>Afficher/masquer les informations sur l'image</td></tr>
</table>

<h3>Analyse</h3>
<table>
<tr><td width="120"><b>A</b></td><td>Inspection des artéfacts</td></tr>
</table>

<h3>Fichiers et fenêtres</h3>
<table>
<tr><td width="120"><b>Ctrl+O</b></td><td>Ouvrir un dossier DICOM</td></tr>
<tr><td><b>Ctrl+E</b></td><td>Enregistrer le contrôle et exporter le PDF</td></tr>
<tr><td><b>Ctrl+I</b></td><td>Informations sur l'image</td></tr>
<tr><td><b>F1</b></td><td>Aide</td></tr>
<tr><td><b>Ctrl+Q</b></td><td>Quitter l'application</td></tr>
</table>

<h3>Contrôles souris</h3>
<table>
<tr><td width="120"><b>Molette</b></td><td>Zoom avant/arrière</td></tr>
<tr><td><b>Clic gauche</b></td><td>Déplacer l'image</td></tr>
<tr><td><b>Clic droit</b></td><td>Fenêtrage (↔ Largeur, ↕ Centre)</td></tr>
</table>
"""
        msg = QMessageBox(self)
        msg.setWindowTitle("Raccourcis clavier")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(shortcuts)
        msg.exec()

    def _show_help(self):
        """Show help dialog."""
        help_text = """
<h2>CQ TDM — Aide</h2>

<h3>Introduction</h3>
<p>CQ TDM est un logiciel dédié au contrôle interne périodique des tomodensitomètres, suivant la
décision ANSM du 18/12/2025 (section 9.1.7).</p>

<h3>Utilisation</h3>
<ol>
<li><b>Ouvrir un dossier DICOM</b> (Ctrl+O) contenant les images axiales du fantôme eau</li>
<li><b>Renseigner l'installation</b> (« Nouvelle installation » ou « Modifier… ») : établissement, valeurs de référence pour les tests de stabilité, éléments du registre des opérations</li>
<li><b>Ajuster les coupes d'analyse</b> :
    <ul>
    <li><i>Coupe UH</i> : coupe utilisée pour le nombre CT de l'eau et l'uniformité</li>
    <li><i>Coupes SPB</i> : plage de 10 coupes centrées pour le bruit et le spectre de puissance du bruit</li>
    </ul>
</li>
<li><b>Inspecter les artéfacts</b> (touche A) : vérifiez visuellement l'absence d'artéfacts
cliniquement gênants avec le fenêtrage ANSM (centre 0 UH, largeur 80 UH)</li>
<li><b>Enregistrer le contrôle et exporter le PDF</b> (Ctrl+E) : enregistre le contrôle dans l'historique de l'installation et génère le rapport conforme</li>
</ol>

<h3>Marqueurs du curseur de coupes</h3>
<ul>
<li><b>Triangle jaune</b> : position de la coupe UH (déplaçable)</li>
<li><b>Bande verte</b> : plage des coupes SPB (bords déplaçables)</li>
</ul>

<p><i>Voir le menu Aide › Raccourcis clavier pour la liste complète des raccourcis.</i></p>
"""
        msg = QMessageBox(self)
        msg.setWindowTitle("Aide — CQ TDM")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(help_text)
        msg.exec()

    def _show_report_settings(self):
        """Show report settings dialog (logo, etc.)."""
        dialog = ReportSettingsDialog(self)
        dialog.exec()

    def _show_image_info(self):
        """Show image information dialog."""
        dialog = ImageInfoDialog(self)
        if self._current_image is not None:
            dialog.set_info(self._get_image_info_text())
        dialog.exec()

    def _get_image_info_text(self) -> str:
        """Get image information text for display."""
        if self._current_image is None:
            return "Aucune image chargée"

        img = self._current_image

        # Patient name and IPP
        patient_name = img.patient_name or "—"
        patient_id = img.patient_id or "—"

        # Date and time; the acquisition date falls back through SeriesDate and
        # friends, so ANSM test images with a blank StudyDate are not shown without a date
        datetime_str = self._format_datetime(
            img.acquisition_date, img.acquisition_time or img.study_time)

        # kVp and mA with range detection for series
        kvp_str, ma_str = self._get_kvp_ma_info()

        info_lines = [
            f"Patient : {patient_name}",
            f"IPP : {patient_id}",
            f"Date : {datetime_str}",
            "",
            f"Scanner : {img.manufacturer} {img.model_name}",
            f"Station : {img.station_name or '—'}",
            f"Protocole : {img.series_description or '—'}",
            "",
            f"Tension (kV) : {kvp_str}",
            f"Courant (mA) : {ma_str}",
            f"Temps de rotation : {self._get_rotation_time_str()}",
            f"Pas (pitch) : {self._get_pitch_str()}",
            f"Charge : {format_fr(img.mas, 0)} mAs" if img.mas else "Charge : —",
            f"IDSV (CTDIvol) : {self._get_ctdi_str()}",
            f"Filtre : {img.convolution_kernel or '—'}",
            "",
            f"Matrice : {img.columns} × {img.rows} px",
            f"Taille du pixel : {format_fr(img.pixel_size_mm, 3)} mm",
            f"Épaisseur de coupe : {format_fr(img.slice_thickness, 1)} mm",
            f"Champ de vue : {img.fov:.0f} mm" if img.fov else "Champ de vue : —",
        ]

        exposure_str = self._get_exposure_time_str()
        if exposure_str:
            # Only shown when it is not simply the rotation time again
            info_lines.insert(info_lines.index(f"Filtre : {img.convolution_kernel or '—'}"),
                              f"Temps d'exposition total : {exposure_str}")

        if self._current_series:
            info_lines.append("")
            info_lines.append(f"Nombre de coupes : {self._current_series.num_images}")

        return "\n".join(info_lines)

    def _get_image_overlay_text(self) -> str:
        """Short form of the image information, for the overlay on the image.

        A subset of `_get_image_info_text`: what tells one acquisition from
        another at a glance. Patient name and IPP stay in the dialog, so an
        identity is never left standing on screen or caught in a screenshot.
        """
        if self._current_image is None:
            return ""
        img = self._current_image
        kvp_str, ma_str = self._get_kvp_ma_info()
        # "(toutes coupes)" is noise once the values are on one compact line;
        # the modulation range, which does matter, is kept
        kvp_str = kvp_str.replace(" (toutes coupes)", "")
        ma_str = ma_str.replace(" (toutes coupes)", "")
        # Same date as the control itself: ANSM test images have a blank StudyDate
        date_str = self._format_datetime(
            img.acquisition_date, img.acquisition_time or img.study_time)
        # Some ANSM test images carry no date tag at all: drop the line rather
        # than open the overlay with "date inconnue"
        if date_str.startswith(UNKNOWN_DATE):
            date_str = date_str[len(UNKNOWN_DATE):].strip()
        lines = [
            date_str,
            f"{img.manufacturer or ''} {img.model_name or ''}".strip(),
            img.series_description or "",
            f"{kvp_str} kV · {ma_str} mA · {self._get_rotation_time_str()}/tour"
            + (f" · {format_fr(img.mas, 0)} mAs" if img.mas else ""),
            " · ".join(part for part in (
                f"Pas {format_fr(img.pitch, 3)}" if img.pitch > 0 else "",
                f"IDSV {self._get_ctdi_str()}" if img.ctdi_vol > 0 else "",
            ) if part),
            f"{img.convolution_kernel or 'filtre non renseigné'} · {img.columns} × {img.rows} px"
            f" · {format_fr(img.pixel_size_mm, 3)} mm/px",
            f"Coupe {format_fr(img.slice_thickness, 1)} mm"
            + (f" · champ {img.fov:.0f} mm" if img.fov else ""),
        ]
        if self._current_series:
            lines[-1] += f" · {self._current_series.num_images} coupes"
        return "\n".join(line for line in lines if line)

    def _reset_slices_to_saved(self):
        """Put the HU slice and SPB range back to the values saved with the device."""
        if self._saved_hu_slice is not None:
            self.image_viewer.set_hu_slice_index(self._saved_hu_slice)
        if self._saved_nps_start is not None and self._saved_nps_end is not None:
            self.image_viewer.set_nps_slice_range(self._saved_nps_start, self._saved_nps_end)
        self._check_slice_values_modified()

    def _on_field_changed(self, text: str = ""):
        """Handle field value change - update instance variables and check modifications."""
        # Update instance variables from current field values
        self._hospital_name = self._edit_hospital_name.text()
        self._hospital_location = self._edit_hospital_location.text()
        self._device_name = self._edit_device_name.text()
        self._commissioning_date = self._edit_commissioning_date.text()
        self._serial_number = self._edit_serial_number.text()
        self._inventory_number = self._edit_inventory_number.text()

        self._update_install_summary()

    def _check_any_value_modified(self) -> bool:
        """True when the slices chosen in the viewer differ from the saved ones.

        The installation fields themselves are edited and saved in the
        "Gestion des installations" window, so only the slice selection can be
        pending here.
        """
        if self._current_device is None or self._current_series is None:
            return False
        current_hu = self.image_viewer.get_hu_slice_index()
        current_nps_start, current_nps_end = self.image_viewer.get_nps_slice_range()
        if self._saved_hu_slice is not None and current_hu != self._saved_hu_slice:
            return True
        if self._saved_nps_start is not None and current_nps_start != self._saved_nps_start:
            return True
        if self._saved_nps_end is not None and current_nps_end != self._saved_nps_end:
            return True
        return False

    def _check_slice_values_modified(self):
        """Refresh the summary and the reset button after a slice change."""
        self._update_install_summary()
        if hasattr(self, "_btn_reset_slices"):
            _, _, differs = self._slice_selection_state()
            self._btn_reset_slices.setEnabled(differs)

    def _open_dicom_folder(self):
        """Open a folder containing DICOM files."""
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "Ouvrir un dossier DICOM",
            ""
        )
        if folder_path:
            self._load_dicom_folder(folder_path)

    # ---- database shared with other workstations ----

    # Seconds between two checks of the database file when the window is activated
    DATABASE_CHECK_INTERVAL_S = 5.0

    def changeEvent(self, event):
        """Coming back to the window: take in what other workstations recorded meanwhile."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._refresh_database()

    def _refresh_database(self, force: bool = False):
        """Re-read the installations database if another workstation changed it.

        Costs one `stat` of the file, at most every DATABASE_CHECK_INTERVAL_S
        unless `force` is set.
        """
        now = time.monotonic()
        if not force and now - self._database_checked_at < self.DATABASE_CHECK_INTERVAL_S:
            return
        self._database_checked_at = now
        try:
            changed = self._device_db.refresh()
        except Exception:
            logger.exception("Refreshing the installations database failed")
            return
        if self._device_db.take_foreign_changes() or changed:
            self._on_database_refreshed()

    def _on_database_refreshed(self):
        """Show the state of the database after changes made on another workstation."""
        current = self._current_device
        if current is not None and self._device_db.get_device(current.device_id) is not current:
            current = None  # deleted on another workstation
        if current is not None:
            # Objects are updated in place: only what shows them needs redrawing.
            # The slices chosen in the viewer are the user's, they are left alone.
            self._current_device = current
            self._load_device_config(current, apply_slices=False)
            self._refresh_device_combo()
        elif self._current_image is not None:
            # Its installation may just have been created elsewhere
            self._current_device = None
            self._try_auto_detect_device()
            self._refresh_device_combo()
        else:
            self._select_device(self._default_device())
        self._update_results_display()
        self.statusbar.showMessage(
            "Base de données des installations mise à jour depuis un autre poste", 6000)

    def dragEnterEvent(self, event):
        """Accept drag events containing folders or files."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Handle drop - open the first folder (or parent folder of first file)."""
        urls = event.mimeData().urls()
        if not urls:
            return
        path = Path(urls[0].toLocalFile())
        folder = path if path.is_dir() else path.parent
        self._load_dicom_folder(str(folder))

    @contextmanager
    def _busy(self, message: str):
        """Wait cursor and status message around an operation that blocks the window."""
        self.statusbar.showMessage(message)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        # Let the message and the cursor be drawn before the blocking call,
        # without letting a click start something else in the meantime
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()

    def _warn_empty_folder(self, series: DicomSeries):
        """Explain why a folder gave no slice to analyse."""
        self.statusbar.showMessage("Aucune coupe TDM utilisable dans ce dossier", 8000)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Chargement DICOM")
        if series.load_errors:
            box.setText("Aucune coupe de ce dossier n'a pu être utilisée.")
            box.setInformativeText(self._summarise_reasons(series.load_errors))
        elif series.skipped:
            box.setText("Ce dossier ne contient aucune coupe TDM axiale.")
            box.setInformativeText(self._summarise_reasons(series.skipped))
        else:
            box.setText("Aucun fichier DICOM trouvé dans ce dossier.")
            box.setInformativeText(
                "Vérifiez qu'il s'agit du dossier de la série exportée "
                "(fichiers .dcm ou sans extension).")
        details = series.load_errors + series.skipped
        if details:
            box.setDetailedText("\n".join(details))
        box.exec()

    @staticmethod
    def _summarise_reasons(entries: list[str], limit: int = 4) -> str:
        """The distinct reasons of "file : reason" entries, most frequent first, with counts."""
        counts: dict[str, int] = {}
        for entry in entries:
            reason = entry.split(" : ", 1)[-1]
            counts[reason] = counts.get(reason, 0) + 1
        ordered = sorted(counts.items(), key=lambda item: -item[1])
        lines = [f"• {reason} ({n} fichier{'s' if n > 1 else ''})" for reason, n in ordered[:limit]]
        if len(ordered) > limit:
            lines.append("• …")
        return "\n".join(lines)

    def _choose_series(self, series: DicomSeries) -> str | None:
        """Ask which series of the folder to analyse; None when cancelled."""
        infos = series.available_series
        labels = [info.label() for info in infos]
        label, ok = QInputDialog.getItem(
            self, "Plusieurs séries dans ce dossier",
            "Ce dossier contient plusieurs séries de coupes TDM.\n"
            "Une seule série est analysée à la fois ; choisissez laquelle :",
            labels, 0, False)
        if not ok:
            return None
        return infos[labels.index(label)].series_uid

    def _load_dicom_folder(self, folder_path: str, series_uid: str | None = None) -> bool:
        """Load one DICOM series from a folder; True when a series was loaded.

        `series_uid` designates the series to load when the folder holds
        several (reload of a recorded control); otherwise the user is asked.
        """
        try:
            with self._busy(f"Chargement du dossier : {folder_path}"):
                series = load_dicom_folder(folder_path, series_uid)

            if series.is_empty:
                self._warn_empty_folder(series)
                return False

            if series_uid is None and series.other_series:
                chosen = self._choose_series(series)
                if chosen is None:
                    self.statusbar.showMessage("Chargement annulé", 5000)
                    return False
                if chosen != series.series_uid:
                    with self._busy(f"Chargement du dossier : {folder_path}"):
                        series = load_dicom_folder(folder_path, chosen)

            self._current_series = series
            self._current_folder = str(Path(folder_path).resolve())
            self._manual_control_date = ""
            self._control_type = ""  # offered again from the installation's last control
            # Show where the series came from, over the top-right of the image
            self.image_viewer.set_dicom_folder(self._current_folder)
            # Reset all results so a failed analysis on the new series cannot leave
            # the previous series' numbers in the panel or in the PDF
            self._current_results = None
            self._nps_results = None
            self._artifact_result = None  # Reset artifact result for new series
            self._artifact_description = ""
            # Notes belong to one control: do not carry them over to the next series
            self._user_notes = ""
            self.btn_notes.setText("Ajouter une observation")
            # Drop the overlays of the previous series until the new analysis draws its own
            self.image_viewer.viewer.clear_rois()
            self.btn_export.setEnabled(False)
            self._update_results_display()

            # Enable artifact inspection button
            self.btn_artifact.setEnabled(True)

            # Update slice slider (this also sets default HU/NPS slice values)
            self.image_viewer.set_slice_count(series.num_images)

            # Set default slice to middle (same as default HU position)
            middle_slice = (series.num_images - 1) // 2
            self.image_viewer.set_current_slice(middle_slice)
            self._current_image = series.images[middle_slice]

            # Display middle image and fit to view
            self.image_viewer.set_image(self._current_image, fit_to_view=True)
            self.image_viewer.set_image_info(self._get_image_overlay_text())
            self._update_debug_overlay()

            # Try to auto-detect device from database
            self._try_auto_detect_device()

            # Trigger initial analysis by emitting signals from current spinbox values
            # (set_slice_count already set default values, now trigger the analysis)
            hu_slice = self.image_viewer.get_hu_slice_index()
            nps_start, nps_end = self.image_viewer.get_nps_slice_range()
            self._on_hu_slice_changed(hu_slice)
            self._on_nps_range_changed(nps_start, nps_end)

            n = series.num_images
            summary = f"{Path(folder_path).name} · {n} coupe{'s' if n > 1 else ''}"
            if series.skipped:
                k = len(series.skipped)
                summary += f" · {k} objet{'s' if k > 1 else ''} ignoré{'s' if k > 1 else ''}"
            if series.load_errors:
                k = len(series.load_errors)
                summary += f" · {k} fichier{'s' if k > 1 else ''} non utilisable{'s' if k > 1 else ''}"
            self._series_status.setText(summary)
            self._series_status.setToolTip("\n".join(series.load_errors + series.skipped))
            loaded = f"{n} image{'s' if n > 1 else ''} chargée{'s' if n > 1 else ''} depuis {Path(folder_path).name}"
            if not self._has_dicom_identity(self._current_image):
                loaded += " — série sans identification du scanner : sélectionnez son installation"
            self.statusbar.showMessage(loaded, 8000)
            # The history is the home view; with a series, the results are the point
            self._results_tabs.setCurrentIndex(self._results_tabs.indexOf(self.results_browser))

            if series.load_errors:
                self._warn_unusable_files(series)
            return True

        except Exception as e:
            logger.exception("Loading %s failed", folder_path)
            self.statusbar.showMessage(f"Erreur de chargement : {e}", 12000)
            QMessageBox.critical(self, "Chargement DICOM",
                                 f"Impossible de charger le dossier DICOM :\n{e}")
            return False

    def _warn_unusable_files(self, series: DicomSeries):
        """Tell the user that files of the folder are missing from the loaded series.

        The number of slices matters (10 slices for the SPB, artifacts to be
        inspected on every slice): a series must not be analysed short of some
        of its images without the operator knowing.
        """
        n, k = series.num_images, len(series.load_errors)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Chargement DICOM")
        box.setText(
            f"{n} image{'s' if n > 1 else ''} chargée{'s' if n > 1 else ''}, "
            f"{k} fichier{'s' if k > 1 else ''} non utilisable{'s' if k > 1 else ''}.")
        box.setInformativeText(
            self._summarise_reasons(series.load_errors)
            + "\n\nVérifiez que la série chargée est complète avant de l'analyser.")
        box.setDetailedText("\n".join(series.load_errors))
        box.exec()

    @staticmethod
    def _has_dicom_identity(image: DicomImage | None) -> bool:
        """False when the image names no scanner at all (anonymised series)."""
        if image is None:
            return False
        return bool(DeviceConfig.generate_id(image.manufacturer or "", image.model_name or "",
                                             image.station_name or "", image.device_serial_number or ""))

    def _on_slice_changed(self, index: int):
        """Handle slice selection change."""
        if self._current_series and 0 <= index < self._current_series.num_images:
            self._current_image = self._current_series.images[index]
            self.image_viewer.set_image(self._current_image)
            # Slice-dependent fields (time, thickness) follow the displayed image
            self.image_viewer.set_image_info(self._get_image_overlay_text())
            # Update debug overlay if enabled
            if self._debug_mode:
                self._update_debug_overlay()

    def _get_rotation_time_str(self) -> str:
        """Gantry rotation time of the current image, or "—"."""
        if self._current_image is None:
            return "—"
        rotation = self._current_image.rotation_time
        return f"{format_fr(rotation, 2)} s" if rotation > 0 else "—"

    def _get_exposure_time_str(self) -> str:
        """Exposure time, only when it says something the rotation time does not.

        The two agree on most scanners. When they differ the tag holds the
        duration of the whole acquisition, which is worth showing separately.
        """
        if self._current_image is None:
            return ""
        img = self._current_image
        if img.exposure_time <= 0:
            return ""
        seconds = img.exposure_time / 1000.0
        rotation = img.rotation_time
        if rotation > 0 and abs(seconds - rotation) < 0.005:
            return ""
        return f"{format_fr(seconds, 2)} s"

    def _get_pitch_str(self) -> str:
        """Pitch of the current image; axial acquisitions simply have none."""
        if self._current_image is None:
            return "—"
        img = self._current_image
        if img.pitch > 0:
            return format_fr(img.pitch, 3)
        if img.acquisition_type.upper().startswith("SEQUENCE"):
            return "acquisition séquentielle"
        return "—"

    def _get_ctdi_str(self) -> str:
        """CTDIvol in mGy with its phantom, which the value cannot be read without."""
        if self._current_image is None or self._current_image.ctdi_vol <= 0:
            return "—"
        img = self._current_image
        phantom = f" ({img.ctdi_phantom})" if img.ctdi_phantom else ""
        return f"{format_fr(img.ctdi_vol, 2)} mGy{phantom}"

    def _get_kvp_ma_info(self) -> tuple[str, str]:
        """Get kVp and mA info, detecting modulation across slices."""
        if self._current_series and self._current_series.num_images > 1:
            kvp_values = [img.kvp for img in self._current_series.images if img.kvp]
            ma_values = [img.tube_current for img in self._current_series.images if img.tube_current]

            if kvp_values:
                kvp_min, kvp_max = min(kvp_values), max(kvp_values)
                if kvp_min == kvp_max:
                    kvp_str = f"{kvp_min:.0f} (toutes coupes)"
                else:
                    kvp_str = f"{kvp_min:.0f}-{kvp_max:.0f} (modulation)"
            else:
                kvp_str = "—"

            if ma_values:
                ma_min, ma_max = min(ma_values), max(ma_values)
                if ma_min == ma_max:
                    ma_str = f"{ma_min:.0f} (toutes coupes)"
                else:
                    ma_str = f"{ma_min:.0f}-{ma_max:.0f} (modulation)"
            else:
                ma_str = "—"
        else:
            # Single image
            img = self._current_image
            kvp_str = f"{img.kvp:.0f}" if img.kvp else "—"
            ma_str = f"{img.tube_current:.0f}" if img.tube_current else "—"

        return kvp_str, ma_str

    def _format_datetime(self, date_str: str, time_str: str) -> str:
        """Format DICOM date and time to readable format."""
        date_part = UNKNOWN_DATE
        if date_str and len(date_str) >= 8:
            date_part = f"{date_str[6:8]}/{date_str[4:6]}/{date_str[:4]}"

        time_part = ""
        if time_str and len(time_str) >= 4:
            # DICOM time format: HHMMSS.FFFFFF
            hours = time_str[0:2]
            minutes = time_str[2:4]
            seconds = time_str[4:6] if len(time_str) >= 6 else "00"
            time_part = f" {hours}:{minutes}:{seconds}"

        return date_part + time_part

    def _export_pdf(self):
        """Record the control on screen in the history and export its PDF report."""
        if self._current_image is None:
            QMessageBox.warning(self, "Export du rapport", "Aucune image chargée.")
            return

        if self._current_results is None and self._nps_results is None:
            QMessageBox.warning(self, "Export du rapport", "Aucune analyse effectuée.")
            return

        if self._current_device is None:
            if not ask(
                    self, "Installation non enregistrée",
                    "L'installation n'est pas enregistrée : le rapport PDF sera exporté mais le contrôle "
                    f"ne sera pas ajouté à l'historique.\n\nEnregistrez d'abord l'installation via "
                    f"« {NEW_INSTALL_LABEL} » pour conserver les résultats.",
                    "Exporter quand même", "Annuler"):
                return

        if not self._confirm_incomplete_export():
            return

        # Type of control, names, and the date when the images carry none: a
        # control is never dated from the day of the analysis
        if not self._ask_export_options():
            return
        control_date, control_date_manual = self._control_date()

        # Default filename with device info and the scan date (same date as the history)
        default_name = generate_report_filename(
            device_name=self._device_name,
            inventory_number=self._inventory_number,
            date=control_date,
            control_type=self._control_type,
        )

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer le rapport PDF",
            default_name,
            "Rapports PDF (*.pdf)"
        )
        if not file_path:
            return
        try:
            with self._busy("Génération du rapport…"):
                self._generate_report(file_path, control_date, control_date_manual)
        except Exception as e:
            logger.exception("Report generation failed (%s)", file_path)
            self.statusbar.showMessage(f"Erreur d'export : {e}", 12000)
            QMessageBox.critical(self, "Export du rapport", f"Le rapport n'a pas pu être généré :\n{e}")
            return
        history_msg = self._record_run_in_history(file_path)
        self.statusbar.showMessage(f"Rapport exporté : {file_path}{history_msg}", 12000)

        # Open PDF directly with default viewer
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))

    def _default_control_type(self) -> str:
        """Type of control offered at export: the one of the installation's last control."""
        device = self._current_device
        if device is not None:
            for run in reversed(device.runs):
                if run.control_type:
                    return run.control_type
        return DEFAULT_CONTROL_TYPE

    def _ask_export_options(self) -> bool:
        """Ask the type of control, the names and, if the images have none, the date."""
        has_dicom_date = bool(self._control_date()[0]) and not self._manual_control_date
        dialog = ExportDialog(
            self,
            control_type=self._control_type or self._default_control_type(),
            performed_by=self._performed_by,
            validated_by=self._validated_by,
            ask_date=not has_dicom_date,
            manual_date=self._manual_control_date,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self._control_type = dialog.control_type
        self._performed_by = dialog.performed_by
        self._validated_by = dialog.validated_by
        if dialog.manual_date:
            self._manual_control_date = dialog.manual_date
            self._update_results_display()
        config = get_app_config()
        if (config.last_performed_by, config.last_validated_by) != (self._performed_by, self._validated_by):
            config.last_performed_by, config.last_validated_by = self._performed_by, self._validated_by
            try:
                save_app_config()
            except OSError:
                pass  # read-only settings: the names are simply not offered next time
        return True

    def _generate_report(self, file_path: str, control_date: str, control_date_manual: bool):
        """Write the PDF report of the control on screen to `file_path`."""
        # Create artifact result if inspection was performed
        artifact_result = None
        if self._artifact_result is not None:
            artifact_result = ArtifactInspectionResult(
                artifacts_present=self._artifact_result,
                description=self._artifact_description,
            )

        ref_noise, ref_nps_freq = self._reference_values()

        # Get NPS middle slice image for the report
        nps_middle_image = None
        nps_slice_range = None
        if self._nps_results is not None and self._current_series is not None:
            nps_start, nps_end = self.image_viewer.get_nps_slice_range()
            nps_slice_range = (nps_start, nps_end)
            # Same slice as analyze_nps uses for centre detection: slices[len // 2]
            nps_middle_index = nps_start + (nps_end - nps_start + 1) // 2
            if 0 <= nps_middle_index < self._current_series.num_images:
                nps_middle_image = self._current_series.images[nps_middle_index]

        # Report on the slice that was analysed (HU slice), not the one
        # currently browsed in the viewer
        report_image = self._analysed_image()

        # Previous controls only: an earlier export of this same series is
        # replaced by this one in the history, so it must not appear as a
        # separate past control in the report
        history = []
        if self._current_device is not None and get_app_config().report_include_history:
            current_uid = report_image.series_instance_uid
            history = [r for r in self._current_device.runs
                       if not (current_uid and r.run_id == current_uid)]

        generate_pdf_report(
            file_path,
            report_image,
            self._current_results,
            self._nps_results,
            artifact_result,
            hospital_name=self._hospital_name,
            hospital_location=self._hospital_location,
            device_name=self._device_name,
            commissioning_date=self._commissioning_date,
            serial_number=self._serial_number,
            inventory_number=self._inventory_number,
            reference_noise=ref_noise,
            reference_nps_freq=ref_nps_freq,
            nps_image=nps_middle_image,
            logo_path=get_app_config().report_logo_path or None,
            logo_scale=get_app_config().report_logo_scale,
            notes=self._user_notes,
            history=history,
            dicom_folder=self._current_folder or "",
            control_date=control_date,
            control_date_manual=control_date_manual,
            control_type=self._control_type,
            performed_by=self._performed_by,
            validated_by=self._validated_by,
            hu_slice_index=(self.image_viewer.get_hu_slice_index()
                            if self._current_series is not None else None),
            nps_slice_range=nps_slice_range,
            total_slices=self._current_series.num_images if self._current_series is not None else None,
            **{attr: getattr(self._current_device, attr, "") or ""
               for attr in ("phantom_brand", "phantom_model", "phantom_serial",
                            "clinical_protocol_origin", "reconstruction_algorithm")},
        )

    def _analysed_image(self) -> DicomImage | None:
        """The image of the HU slice: the one the control is measured, dated and reported on."""
        img = self._current_image
        if self._current_series is not None:
            hu_index = self.image_viewer.get_hu_slice_index()
            if 0 <= hu_index < self._current_series.num_images:
                img = self._current_series.images[hu_index]
        return img

    def _control_date(self) -> tuple[str, bool]:
        """(ISO date of the control, typed by hand?); ("", False) when it is unknown.

        The date comes from the images. When they carry none (anonymised
        series), it is the one the user typed, never the day of the analysis.
        """
        img = self._analysed_image()
        iso = dicom_date_to_iso(img.acquisition_date) if img is not None else ""
        if iso:
            return iso, False
        return self._manual_control_date, bool(self._manual_control_date)

    def _ask_control_date(self) -> bool:
        """Ask the date of a control whose images carry none; False when cancelled."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Date du contrôle")
        layout = QVBoxLayout(dialog)
        label = QLabel(
            "Les images de cette série ne contiennent aucune date d'acquisition.\n"
            "Indiquez la date à laquelle le fantôme a été scanné : elle figurera dans "
            "le rapport et dans l'historique, avec la mention « saisie manuellement ».")
        label.setWordWrap(True)
        layout.addWidget(label)
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("dd/MM/yyyy")
        previous = QDate.fromString(self._manual_control_date, "yyyy-MM-dd")
        edit.setDate(previous if previous.isValid() else QDate.currentDate())
        edit.setMaximumDate(QDate.currentDate())
        layout.addWidget(edit)
        buttons = french_button_box("Valider", "Annuler")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self._manual_control_date = edit.date().toString("yyyy-MM-dd")
        self._update_results_display()
        return True

    def _ensure_control_date(self) -> bool:
        """True when the control has a date, asking for it if the images carry none."""
        if self._control_date()[0]:
            return True
        return self._ask_control_date()

    def _phantom_warnings(self) -> list[str]:
        """What to tell the user when the phantom wall was not found."""
        warnings = []
        if self._current_results is not None and not self._current_results.phantom_detected:
            warnings.append("fantôme non détecté sur la coupe UH")
        if self._nps_results is not None and not self._nps_results.phantom_detected:
            warnings.append("fantôme non détecté sur la coupe médiane de la plage SPB")
        return warnings

    def _reference_values(self) -> tuple[float | None, float | None]:
        """Reference noise and SPB frequency from the text fields (works without a saved device)."""
        ref_noise_text = self._edit_ref_noise.text().strip()
        ref_nps_text = self._edit_ref_nps_freq.text().strip()
        return (parse_float_fr(ref_noise_text) if ref_noise_text else None,
                parse_float_fr(ref_nps_text) if ref_nps_text else None)

    def _measured_noise(self) -> float | None:
        """Noise of the control on screen: mean σ of the SPB ROIs; None without SPB analysis."""
        return self._nps_results.noise if self._nps_results is not None else None

    def _current_statuses(self) -> dict[str, str]:
        """Status of each test of the control on screen, plus ``overall`` (same rule as the PDF)."""
        r = self._current_results
        ref_noise, ref_nps_freq = self._reference_values()
        return evaluate_measurements(
            r.water_ct_number if r else None,
            r.uniformity if r else None,
            self._measured_noise(),
            self._nps_results.mean_frequency if self._nps_results else None,
            self._artifact_result,
            ref_noise,
            ref_nps_freq,
        )

    def _confirm_incomplete_export(self) -> bool:
        """Before an export, list the tests that were not judged; False to cancel.

        The report of such a control cannot say "CONFORME": the user either
        completes the control or exports it knowingly.
        """
        statuses = self._current_statuses()
        missing = pending_reasons(statuses, nps_measured=self._nps_results is not None,
                                  noise_measured=self._nps_results is not None)
        undetected = self._phantom_warnings()
        if not missing and not undetected:
            return True
        parts = []
        if missing:
            text = "Ce contrôle est incomplet :\n\n" + "\n".join(f"• {m}" for m in missing)
            if statuses["overall"] == INCOMPLETE:
                text += ("\n\nLe rapport portera la mention « CONTRÔLE INCOMPLET » "
                         "à la place du verdict de conformité.")
            else:
                text += "\n\nLe rapport signalera ces tests comme non réalisés."
            parts.append(text)
        if undetected:
            parts.append(
                "Les ROI ont été placées sans repérer la paroi du fantôme :\n\n"
                + "\n".join(f"• {w}" for w in undetected)
                + "\n\nVérifiez leur position sur l'image : les mesures ne sont valables que "
                "si les ROI sont dans l'eau. Le rapport le signalera.")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Contrôle incomplet" if missing else "Fantôme non détecté")
        box.setText("\n\n".join(parts))
        export = box.addButton("Exporter quand même", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Compléter le contrôle" if missing else "Annuler",
                               QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is export

    def _export_nps_rois_json(self):
        """Export NPS ROI positions to JSON file."""
        import json

        if self._nps_results is None:
            QMessageBox.warning(self, "Export des ROI SPB", "Aucune analyse SPB effectuée.")
            return

        # Default filename
        from datetime import datetime
        default_name = f"NPS_ROIs_Position_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter positions ROIs SPB",
            default_name,
            "Fichiers JSON (*.json)"
        )
        if file_path:
            try:
                roi_data = self._nps_results.roi_config.to_dict()
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(roi_data, f, indent='\t', ensure_ascii=False)
                self.statusbar.showMessage(f"Positions des ROI SPB exportées : {file_path}", 12000)
            except Exception as e:
                self.statusbar.showMessage(f"Erreur d'export : {e}", 12000)
                QMessageBox.critical(self, "Export des ROI SPB", f"Erreur lors de l'export :\n{e}")

    def _export_hu_rois_json(self):
        """Export HU ROI positions to JSON file."""
        import json

        if self._current_results is None:
            QMessageBox.warning(self, "Export des ROI UH", "Aucune analyse UH effectuée.")
            return

        if self._current_image is None:
            return

        # Get the image at HU slice index for slice location
        hu_slice_index = self.image_viewer.get_hu_slice_index()
        if self._current_series and 0 <= hu_slice_index < self._current_series.num_images:
            img = self._current_series.images[hu_slice_index]
        else:
            img = self._current_image

        # Build ROI data structure for circular ROIs (positions only)
        roi_data = {
            "type_of_file": "HU",
            "phantom": img.series_description or "",
            "measurement_date": img.study_date or "",
            "DFOV": img.reconstruction_diameter or (img.columns * img.pixel_size_mm),
            "pixel_size": img.pixel_size_mm,
            "width_in_pixel": float(img.columns),
            "commentaire": "HU - ROI's position given in pixel (circular ROIs), slice in mm",
            "slice_position_mm": img.slice_location,
            "ROI": [
                {
                    "name": "Centre",
                    "X": float(self._current_results.central.center_col),
                    "Y": float(self._current_results.central.center_row),
                    "radius": float(self._current_results.central.radius),
                },
                {
                    "name": "12h",
                    "X": float(self._current_results.top.center_col),
                    "Y": float(self._current_results.top.center_row),
                    "radius": float(self._current_results.top.radius),
                },
                {
                    "name": "3h",
                    "X": float(self._current_results.right.center_col),
                    "Y": float(self._current_results.right.center_row),
                    "radius": float(self._current_results.right.radius),
                },
                {
                    "name": "6h",
                    "X": float(self._current_results.bottom.center_col),
                    "Y": float(self._current_results.bottom.center_row),
                    "radius": float(self._current_results.bottom.radius),
                },
                {
                    "name": "9h",
                    "X": float(self._current_results.left.center_col),
                    "Y": float(self._current_results.left.center_row),
                    "radius": float(self._current_results.left.radius),
                },
            ]
        }

        # Default filename
        from datetime import datetime
        default_name = f"HU_ROIs_Position_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter positions ROIs UH",
            default_name,
            "Fichiers JSON (*.json)"
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(roi_data, f, indent='\t', ensure_ascii=False)
                self.statusbar.showMessage(f"Positions des ROI UH exportées : {file_path}", 12000)
            except Exception as e:
                self.statusbar.showMessage(f"Erreur d'export : {e}", 12000)
                QMessageBox.critical(self, "Export des ROI UH", f"Erreur lors de l'export :\n{e}")

    def _display_rois(self, rois, slice_index: int):
        """Display ROIs on the image viewer."""
        from PySide6.QtGui import QColor

        roi_list = []

        # Central ROI - yellow
        roi_list.append(ROI(
            center_x=rois.central.center_col,
            center_y=rois.central.center_row,
            radius=rois.central.radius,
            name="C",
            color=QColor(255, 255, 0),
        ))

        # Peripheral ROIs - cyan
        peripheral_color = QColor(0, 255, 255)
        for roi_def, label in [
            (rois.top, "12h"),
            (rois.right, "3h"),
            (rois.bottom, "6h"),
            (rois.left, "9h"),
        ]:
            roi_list.append(ROI(
                center_x=roi_def.center_col,
                center_y=roi_def.center_row,
                radius=roi_def.radius,
                name=label,
                color=peripheral_color,
            ))

        # Set water ROIs for the specified slice
        self.image_viewer.viewer.set_water_rois(roi_list, slice_index=slice_index)
        self.image_viewer.set_water_roi_marker(slice_index)

    def _display_nps_roi(self, nps_result: NPSResult, start_slice: int, end_slice: int):
        """Display all 8 NPS ROIs on the image viewer."""
        from PySide6.QtGui import QColor

        if self._current_series is None:
            return

        # Get ROI positions from the analysis result
        roi_positions = nps_result.roi_config.rois
        half_size = nps_result.roi_size // 2

        # Create ROI objects for display; ROIs clipped by the image border were
        # not measured and are drawn in red so the overlay matches the analysis
        roi_list = []
        skipped = set(nps_result.skipped_rois)

        for i, roi_pos in enumerate(roi_positions):
            measured = i not in skipped
            roi = ROI(
                center_x=roi_pos.x,
                center_y=roi_pos.y,
                radius=half_size,
                name=f"SPB{i+1}" if measured else f"SPB{i+1} (ignorée)",
                color=QColor(0, 255, 0) if measured else QColor(255, 80, 80),
                is_square=True,
            )
            roi_list.append(roi)

        if skipped:
            self.statusbar.showMessage(
                f"SPB : {len(skipped)} ROI hors image ignorée(s) — recentrer le fantôme", 8000)

        # Set NPS ROIs with slice range from parameters
        self.image_viewer.viewer.set_nps_rois(roi_list, slice_range=(start_slice, end_slice))
        self.image_viewer.set_nps_roi_markers(start_slice, end_slice)

    def _on_hu_slice_changed(self, slice_index: int):
        """Handle HU analysis slice change - debounce before re-running analysis."""
        if self._current_series is None:
            return

        # Store pending slice and restart debounce timer
        self._pending_hu_slice = slice_index
        self._hu_debounce_timer.start()

        # Check if values differ from saved
        self._check_slice_values_modified()

    def _run_debounced_hu_analysis(self):
        """Run HU analysis after debounce delay."""
        if self._current_series is None or self._pending_hu_slice is None:
            return

        slice_index = self._pending_hu_slice
        self._pending_hu_slice = None

        try:
            # Get the image for HU analysis
            if 0 <= slice_index < self._current_series.num_images:
                hu_image = self._current_series.images[slice_index]
            else:
                return

            # Run HU analysis, with the installation's frozen ROI geometry when it applies
            geometry, mismatch = self._geometry_for_analysis(hu_image)
            rois = calculate_rois(hu_image, geometry=geometry)
            results = analyze_water_phantom(hu_image, rois)
            self._current_results = results
            self._hu_geometry_mismatch = mismatch

            # Display ROIs
            self._display_rois(rois, slice_index)

            # Update results display
            self._update_results_display()
            self.btn_export.setEnabled(True)

        except Exception as e:
            logger.exception("HU analysis failed on slice %s", slice_index)
            self._current_results = None
            # No measurement: do not keep drawing ROIs that no longer mean anything
            self.image_viewer.viewer.clear_water_rois()
            self._update_results_display()
            self.btn_export.setEnabled(self._nps_results is not None)
            self.statusbar.showMessage(f"Erreur d'analyse UH : {e}", 12000)

    def _on_nps_range_changed(self, start: int, end: int):
        """Handle NPS range change - debounce before re-running analysis."""
        if self._current_series is None:
            return

        # Store pending range and restart debounce timer
        self._pending_nps_range = (start, end)
        self._nps_debounce_timer.start()

        # Check if values differ from saved
        self._check_slice_values_modified()

    def _run_debounced_nps_analysis(self):
        """Run NPS analysis after debounce delay."""
        if self._current_series is None or self._pending_nps_range is None:
            return

        start, end = self._pending_nps_range
        self._pending_nps_range = None

        try:
            # Run NPS analysis with new range
            geometry, _mismatch = self._geometry_for_analysis(self._current_series.images[start])
            with self._busy("Analyse du SPB en cours…"):
                nps_result = analyze_nps(
                    self._current_series, slice_range=(start, end), geometry=geometry)
            self._nps_results = nps_result

            # Display NPS ROI
            self._display_nps_roi(nps_result, start, end)

            # Update results display
            self._update_results_display()
            self.btn_export.setEnabled(True)
            self.statusbar.showMessage("Analyse du SPB terminée", 3000)

        except Exception as e:
            logger.exception("SPB analysis failed on slices %s-%s", start, end)
            self._nps_results = None
            self.image_viewer.viewer.clear_nps_rois()
            self._update_results_display()
            self.btn_export.setEnabled(self._current_results is not None)
            self.statusbar.showMessage(f"Erreur d'analyse du SPB : {e}", 12000)

    def _on_reference_field_changed(self):
        """Handle reference field change - debounce before updating comparison."""
        # Always propagate: the history tab evaluates the "En cours" row with these
        # references even before an analysis has run
        self._ref_debounce_timer.start()

    def _run_debounced_reference_update(self):
        """Update reference values and refresh results display after debounce."""
        # Parse reference values from text fields
        ref_noise_text = self._edit_ref_noise.text().strip()
        ref_nps_freq_text = self._edit_ref_nps_freq.text().strip()

        ref_noise = parse_float_fr(ref_noise_text) if ref_noise_text else None
        ref_nps_freq = parse_float_fr(ref_nps_freq_text) if ref_nps_freq_text else None

        # The device object is only updated by "Enregistrer": editing the field
        # must not change what an unrelated save later writes to devices.json
        self.history_panel.set_references(ref_noise, ref_nps_freq)

        # Update results display to show comparison
        self._update_results_display()

    def _toggle_theme(self, checked: bool):
        """Toggle between dark and light theme."""
        config = get_app_config()
        config.theme = "light" if checked else "dark"
        save_app_config()

        QMessageBox.information(
            self, "Thème",
            "Le changement de thème sera appliqué au prochain lancement de l'application."
        )

    def _toggle_debug_mode(self, checked: bool):
        """Toggle debug mode for phantom detection visualization."""
        self._debug_mode = checked
        self._update_debug_overlay()

    def _update_debug_overlay(self):
        """Update the debug overlay on the image viewer."""
        viewer = self.image_viewer.viewer
        viewer.set_debug_mode(self._debug_mode)

        if self._debug_mode and self._current_image is not None:
            # Detect on the HU analysis slice, the one the ROIs are computed
            # from, so the drawn circle is the geometry the ROIs actually use
            image = self._current_image
            if self._current_series is not None:
                hu_index = self.image_viewer.get_hu_slice_index()
                if 0 <= hu_index < self._current_series.num_images:
                    image = self._current_series.images[hu_index]
            geometry = detect_phantom(image)
            viewer.set_debug_phantom(geometry.center, geometry.radius)
        else:
            viewer.set_debug_phantom(None, None)

    def _open_log_folder(self):
        """Open the folder holding cq_tdm.log (and the settings), for support."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from ..core.app_config import AppConfig
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(AppConfig.config_dir())))

    def _show_about(self):
        """Show about dialog."""
        from cq_tdm import __version__
        about_text = f"""
<h2>CQ TDM</h2>
<p><b>Version {__version__}</b></p>

<p>Logiciel pour le contrôle de qualité interne des tomodensitomètres.</p>

<h3>Auteur</h3>
<p>Luis Ammour — <a href="mailto:luis@ammour.net">luis@ammour.net</a></p>

<h3>Cadre réglementaire</h3>
<p>Le logiciel tente d'être conforme à la décision ANSM du 18/12/2025 fixant les modalités
du contrôle de qualité des tomodensitomètres. L'auteur ne garantit pas les résultats.</p>

<p><i>Utilisez ce logiciel à vos risques et périls !</i></p>

<h3>Licence</h3>
<p>CeCILL v2.1 — Licence libre compatible GPL.</p>
"""
        msg = QMessageBox(self)
        msg.setWindowTitle("À propos de CQ TDM")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(about_text)
        msg.exec()

    def _inspect_artifacts(self):
        """Open the artifact inspection dialog."""
        if self._current_image is None:
            QMessageBox.warning(self, "Inspection des artéfacts", "Aucune image chargée.")
            return

        # Build list of images to inspect
        if self._current_series and self._current_series.num_images > 0:
            images = self._current_series.images
            initial_slice = self.image_viewer.get_hu_slice_index()
        else:
            images = [self._current_image]
            initial_slice = 0

        # Show artifact inspection dialog
        result, description = ArtifactInspectionDialog.inspect(images, initial_slice, self)

        if result is not None:
            self._artifact_result = result
            self._artifact_description = description
            self._update_results_display()

            # Update status bar
            if result:
                self.statusbar.showMessage("Artéfacts: Présence détectée (NC)")
            else:
                self.statusbar.showMessage("Artéfacts: Absence confirmée (Conforme)")

    def _edit_notes(self):
        """Open the notes editor dialog."""
        dialog = NotesEditorDialog(self._user_notes, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._user_notes = dialog.get_text()
            # Update button text to indicate notes are present
            if self._user_notes.strip():
                self.btn_notes.setText("Ajouter une observation ✓")
            else:
                self.btn_notes.setText("Ajouter une observation")

    def _get_empty_results_html(self) -> str:
        """Return HTML for empty results state."""
        c = _theme_colors()
        return f"""
        <div style="color: {c['pending']}; text-align: center; padding: 40px;">
            <p>Aucune analyse effectuée</p>
            <p style="font-size: 11px;">Ouvrez un dossier DICOM pour lancer l'analyse</p>
        </div>
        """

    def _update_results_display(self):
        """Update the results browser with current results."""
        html = self._format_results_html()
        self.results_browser.setHtml(html)
        self.history_panel.set_current_run(self._current_run_for_history())
        # A fresh analysis unlocks "définir les valeurs actuelles comme références"
        self._update_install_summary()

    # ---- results history ----

    def _refresh_history(self, device: DeviceConfig | None):
        """Fill the history tab for the selected device."""
        if device is None:
            image = getattr(self, "_current_image", None)
            if image is None:
                placeholder = "Sélectionnez une installation ci-dessus pour afficher l'historique de ses contrôles"
            elif not self._has_dicom_identity(image):
                placeholder = ("Les images n'identifient pas le scanner (série anonymisée) : sélectionnez "
                               "son installation ci-dessus, ou créez-la avec « Nouvelle installation »")
            else:
                placeholder = ("Installation inconnue : créez-la avec « Nouvelle installation » "
                               "pour conserver l'historique de ses contrôles")
            self.history_panel.set_runs([], None, None, placeholder=placeholder)
            return
        self.history_panel.set_runs(list(device.runs), device.reference_noise, device.reference_nps_freq)

    def _default_device(self) -> DeviceConfig | None:
        """Installation to show when nothing designates one: the one used last, or the only one."""
        device = self._device_db.get_device(get_app_config().last_device_id)
        if device is None:
            devices = self._device_db.get_all_devices()
            device = devices[0] if len(devices) == 1 else None
        return device

    def _select_device(self, device: DeviceConfig | None):
        """Make `device` the current installation and refresh everything that shows it."""
        self._current_device = device
        self._load_device_config(device)
        self._refresh_device_combo()
        self._update_install_summary()

    def _remember_device(self, device: DeviceConfig):
        """Record `device` as the installation to reselect at the next startup."""
        config = get_app_config()
        if config.last_device_id == device.device_id:
            return
        config.last_device_id = device.device_id
        try:
            save_app_config()
        except OSError:
            pass  # read-only settings: only the convenience is lost

    def _geometry_for_analysis(self, image) -> tuple[ROIGeometry | None, bool]:
        """(the installation's frozen ROI geometry if it fits this image format, mismatch?).

        Sizes and positions of the ROIs must be identical from one control to
        the next (ANSM). A geometry frozen for another matrix or pixel size is
        unusable: the ROIs are then recomputed (None, True) and the results
        panel says so.
        """
        device = self._current_device
        if device is None or device.roi_geometry is None or image is None:
            return None, False
        if device.roi_geometry.matches(image):
            return device.roi_geometry, False
        return None, True

    def _freeze_roi_geometry(self, geometry: ROIGeometry | None) -> bool:
        """Make `geometry` the installation's ROI geometry (in memory only).

        Returns True when the device changed; the caller saves it.
        """
        if geometry is None or self._current_device is None:
            return False
        self._current_device.roi_geometry = geometry
        # What is on screen was measured with these very sizes: show it as frozen
        if self._current_results is not None and self._current_results.geometry is not None:
            self._current_results.geometry = geometry
        if self._nps_results is not None and self._nps_results.geometry is not None:
            self._nps_results.geometry = geometry
        return True

    @staticmethod
    def _run_geometry(run: QCRun | None) -> ROIGeometry | None:
        """The ROI geometry recorded with a run, marked as frozen from that run."""
        if run is None:
            return None
        geometry = ROIGeometry.from_dict(run.roi_geometry)
        if geometry is not None and not geometry.is_frozen:
            # An undated control freezes the geometry from the day it was recorded
            geometry = geometry.frozen(run.run_date or run.recorded_at[:10])
        return geometry

    def _format_roi_geometry_html(self) -> str:
        """One table row saying where the ROI sizes and positions come from."""
        r = self._current_results
        if r is None:
            return ""
        if self._hu_geometry_mismatch:
            text = ('<span class="nc">recalculées : format d\'image différent de la '
                    'référence (matrice ou taille de pixel)</span>')
        elif r.geometry is not None and r.geometry.is_frozen:
            text = f"figées depuis le contrôle du {iso_to_fr(r.geometry.frozen_date)}"
        else:
            text = "calculées sur cette série (figées à la définition de la référence)"
        return f'<tr><th>Tailles et positions</th><td>{text}</td></tr>'

    def _current_run_for_history(self) -> QCRun | None:
        """Build a QCRun from the measurement on screen, or None if nothing is analysed yet."""
        if self._current_results is None:
            return None
        from datetime import datetime
        from cq_tdm import __version__

        img = self._analysed_image()
        nps_start, nps_end = self.image_viewer.get_nps_slice_range()
        run_date, run_date_manual = self._control_date()
        return QCRun(
            run_date=run_date,
            run_date_manual=run_date_manual,
            control_type=self._control_type,
            performed_by=self._performed_by,
            validated_by=self._validated_by,
            series_uid=img.series_instance_uid if img is not None else "",
            kvp=float(img.kvp) if img is not None and img.kvp else 0.0,
            mas=float(img.mas) if img is not None else 0.0,
            slice_thickness=float(img.slice_thickness) if img is not None else 0.0,
            kernel=img.convolution_kernel if img is not None else "",
            water_ct=self._current_results.water_ct_number,
            uniformity=self._current_results.uniformity,
            noise=self._measured_noise(),
            nps_freq=self._nps_results.mean_frequency if self._nps_results is not None else None,
            artifacts_present=self._artifact_result,
            artifacts_description=self._artifact_description if self._artifact_result else "",
            hu_slice_index=self.image_viewer.get_hu_slice_index(),
            num_slices=self._current_series.num_images if self._current_series is not None else None,
            roi_geometry=(self._current_results.geometry.to_dict()
                          if self._current_results.geometry is not None else None),
            nps_start_slice=nps_start if self._nps_results is not None else None,
            nps_end_slice=nps_end if self._nps_results is not None else None,
            dicom_folder=self._current_folder,
            dicom_folder_rel=relative_to_database(self._current_folder, self._device_db.db_path)
            if self._current_folder else "",
            notes=self._user_notes,
            software_version=__version__,
            recorded_at=datetime.now().isoformat(timespec="seconds"),
        )

    def _record_run_in_history(self, pdf_path: str) -> str:
        """Store the measurement on screen as a QC run of the current device.

        Returns a short status text for the status bar.
        """
        run = self._current_run_for_history()
        if run is None or self._current_device is None:
            return ""
        run.pdf_path = pdf_path
        run.ref_noise, run.ref_nps_freq = self._reference_values()
        # First recorded control: its ROI sizes and positions become the ones
        # every later control must reuse (add_run saves the device). Not when
        # the phantom was not found: a default geometry must never be frozen.
        if self._current_device.roi_geometry is None and not self._phantom_warnings():
            self._freeze_roi_geometry(self._run_geometry(run))
        # Slices saved before their series length was recorded: this control
        # used them, so its series has the length they were chosen on
        device = self._current_device
        if device.slices_series_length is None and device.hu_slice_index is not None \
                and (device.hu_slice_index, device.nps_start_slice, device.nps_end_slice) \
                == (run.hu_slice_index, run.nps_start_slice, run.nps_end_slice):
            device.slices_series_length = run.num_slices
        try:
            replaced = self._device_db.add_run(self._current_device.device_id, run)
        except (OSError, LookupError) as e:
            QMessageBox.warning(
                self, "Historique",
                f"Le rapport a été exporté mais le contrôle n'a pas pu être enregistré dans l'historique :\n{e}")
            return ""
        self._refresh_history(self._current_device)
        self.history_panel.select_run(self._current_device.find_run(run.run_id))
        return " · contrôle remplacé dans l'historique" if replaced else " · contrôle ajouté à l'historique"

    def _confirm_and_set_references(self, noise: float, nps_freq: float | None,
                                    source: str, geometry: ROIGeometry | None = None) -> bool:
        """Ask, then make `noise`/`nps_freq` the reference values of the device.

        `source` names where the values come from, for the question and the
        status bar. `geometry` is the ROI geometry of the reference control; it
        is frozen on the device with the values. Returns True when applied.
        """
        old_noise, old_nps_freq = self._reference_values()
        installation = (self._current_device.display_name() if self._current_device is not None
                        else "cette installation")
        text, accept = reference_question(installation, old_noise, old_nps_freq,
                                          noise, nps_freq, source, geometry is not None)
        if not ask(self, "Valeurs de référence", text, accept, "Annuler"):
            return False

        self._edit_ref_noise.setText(format_fr(noise, 2))
        if nps_freq is not None:
            self._edit_ref_nps_freq.setText(format_fr(nps_freq, 3))
        if self._current_device is not None:
            self._current_device.reference_noise = noise
            # A missing SPB frequency leaves the recorded one alone: the field
            # above is not cleared either, so clearing the device would make the
            # two disagree
            if nps_freq is not None:
                self._current_device.reference_nps_freq = nps_freq
            self._freeze_roi_geometry(geometry)
            try:
                self._device_db.save_device(self._current_device)
            except OSError as e:
                QMessageBox.warning(self, "Valeurs de référence",
                                    f"Impossible d'enregistrer les valeurs de référence :\n{e}")
                return False
        self._update_install_summary()
        self._update_results_display()
        self.statusbar.showMessage(f"Valeurs de référence définies depuis {source}", 5000)
        return True

    def _apply_reference_from_history(self, run: QCRun):
        """Make a past run's noise and SPB frequency the device reference values."""
        if run.noise is None:
            QMessageBox.information(
                self, "Valeurs de référence",
                f"Le contrôle du {run.date_fr()} ne comporte pas de mesure du bruit "
                "(SPB non calculé) : il ne peut pas servir de référence.")
            return
        self._confirm_and_set_references(run.noise, run.nps_freq,
                                         f"contrôle du {run.date_fr()}",
                                         geometry=self._run_geometry(run))

    def _on_install_summary_link(self, href: str):
        """Handle the links embedded in the summary under the device selector."""
        if href == "set-reference":
            self._set_current_as_reference()
        elif href == "save-slices":
            self._save_current_slices()
        elif href == "set-date":
            self._ask_control_date()

    def _save_current_slices(self):
        """Record the slices chosen in the viewer on the current installation."""
        device = self._current_device
        if device is None or self._current_series is None:
            return
        device.hu_slice_index = self.image_viewer.get_hu_slice_index()
        device.nps_start_slice, device.nps_end_slice = self.image_viewer.get_nps_slice_range()
        device.slices_series_length = self._current_series.num_images
        try:
            self._device_db.save_device(device)
        except OSError as e:
            QMessageBox.warning(self, "Installation", f"Impossible d'enregistrer les coupes :\n{e}")
            return
        self._saved_hu_slice = device.hu_slice_index
        self._saved_nps_start, self._saved_nps_end = device.nps_start_slice, device.nps_end_slice
        self._saved_slices_mismatch = None
        self._check_slice_values_modified()
        self.statusbar.showMessage("Coupes mémorisées pour cette installation", 5000)

    def _set_current_as_reference(self):
        """Make the values just measured the reference values.

        The common case for a first control: with no reference recorded there is
        nothing to compare against, and the measured values are what later
        controls must be judged from.
        """
        if self._current_results is None:
            QMessageBox.information(
                self, "Valeurs de référence",
                "Aucune analyse effectuée : lancez l'analyse avant de définir "
                "les valeurs actuelles comme références.")
            return
        if self._nps_results is None:
            QMessageBox.information(
                self, "Valeurs de référence",
                "Le bruit et la fréquence moyenne sont mesurés dans les ROI du SPB : "
                "l'analyse du SPB doit avoir abouti pour définir les valeurs de référence.")
            return
        if self._phantom_warnings():
            QMessageBox.warning(
                self, "Valeurs de référence",
                "Le fantôme n'a pas été détecté : les ROI ne sont pas placées de façon fiable "
                "et ce contrôle ne peut pas servir de référence.")
            return
        # The ROI geometry is frozen "from the control of <date>": a reference
        # control has a date, asked now if the images carry none
        if not self._ensure_control_date():
            return
        self._confirm_and_set_references(self._nps_results.noise, self._nps_results.mean_frequency,
                                         "analyse en cours",
                                         geometry=self._run_geometry(self._current_run_for_history()))

    def _delete_history_run(self, run: QCRun):
        if self._current_device is None:
            return
        try:
            self._device_db.delete_run(self._current_device.device_id, run.run_id)
        except OSError as e:
            QMessageBox.warning(self, "Historique", f"Impossible de supprimer le contrôle :\n{e}")
            return
        self._refresh_history(self._current_device)

    def _edit_corrective_action(self, run: QCRun):
        """Record the corrective action taken after a recorded control."""
        if self._current_device is None:
            return
        dialog = CorrectiveActionDialog(run, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        dialog.apply_to(run)
        try:
            self._device_db.update_run(self._current_device.device_id, run)
        except (OSError, LookupError, ValueError) as e:
            QMessageBox.warning(self, "Historique", f"Impossible d'enregistrer l'action corrective :\n{e}")
            return
        self._refresh_history(self._current_device)
        self.history_panel.select_run(run)

    def _relink_history_pdf(self, run: QCRun, new_path: str):
        if self._current_device is None:
            return
        run.pdf_path = new_path
        try:
            self._device_db.update_run(self._current_device.device_id, run)
        except (OSError, LookupError, ValueError) as e:
            QMessageBox.warning(self, "Historique", f"Impossible d'enregistrer le nouveau chemin :\n{e}")

    # ---- reload the DICOM series of a recorded control ----

    def _load_run_series(self, run: QCRun):
        """Load the DICOM series of a recorded control into the viewer.

        The folder is looked up from the stored path, the path relative to the
        database, and the relocations learnt earlier; if none matches the series
        UID the user can point at the folder or have a directory scanned. The
        run's HU slice and SPB range are then applied so the screen shows the
        control as it was measured. References are the installation's current
        ones: a new export is a new evaluation.
        """
        device = self._current_device
        if device is None:
            return
        folder = resolve_dicom_folder(
            run.series_uid, run.dicom_folder, run.dicom_folder_rel,
            self._device_db.db_path, self._device_db.folder_relocations)
        if folder is None:
            folder = self._ask_run_folder(run)
            if folder is None:
                return
        if not self._load_dicom_folder(str(folder), series_uid=run.series_uid):
            return
        if self._current_series is None or self._current_series.series_uid != run.series_uid:
            QMessageBox.warning(
                self, "Série différente",
                f"Les images chargées depuis {folder} n'appartiennent pas à la série du contrôle "
                f"du {run.date_fr()}.")
            return
        # A series without identity is not recognised on load: it is the series
        # of this installation, since its UID is the one of the recorded control
        if self._current_device is not device:
            self._select_device(device)
        # Images without a date: keep the date the control was recorded with
        if not self._control_date()[0] and run.date is not None:
            self._manual_control_date = run.run_date
            self._update_results_display()
        # Same type of control and same names as when it was recorded
        self._control_type = run.control_type
        self._performed_by = run.performed_by or self._performed_by
        self._validated_by = run.validated_by or self._validated_by
        n = self._current_series.num_images
        needed = max(run.hu_slice_index or 0, run.nps_end_slice or 0)
        if needed >= n:
            QMessageBox.warning(
                self, "Série incomplète",
                f"Le contrôle utilisait la coupe {needed + 1} mais le dossier ne contient que "
                f"{n} image{'s' if n > 1 else ''}. Les coupes ont été ramenées dans la série chargée.")
        if run.hu_slice_index is not None:
            self.image_viewer.set_hu_slice_index(run.hu_slice_index)
        if run.nps_start_slice is not None and run.nps_end_slice is not None:
            self.image_viewer.set_nps_slice_range(run.nps_start_slice, run.nps_end_slice)
        if run.hu_slice_index is not None:
            self.image_viewer.set_current_slice(self.image_viewer.get_hu_slice_index())
            self._on_slice_changed(self.image_viewer.get_hu_slice_index())
        self._results_tabs.setCurrentIndex(0)
        self.statusbar.showMessage(
            f"Série du contrôle du {run.date_fr()} chargée ({n} images) — coupes du contrôle appliquées, "
            "valeurs de référence actuelles de l'installation", 8000)

    def _ask_run_folder(self, run: QCRun) -> Path | None:
        """Offer to locate or search for the series of ``run``; None if cancelled."""
        where = run.dicom_folder or "(dossier non enregistré pour ce contrôle)"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Images DICOM introuvables")
        box.setText(f"Le dossier DICOM du contrôle du {run.date_fr()} est introuvable :\n{where}")
        box.setInformativeText(
            "Indiquez son nouvel emplacement, ou laissez CQ TDM le rechercher dans un dossier "
            "d'archive. La série est vérifiée par son identifiant DICOM avant toute utilisation.")
        btn_locate = box.addButton("Localiser…", QMessageBox.ButtonRole.AcceptRole)
        btn_search = box.addButton("Rechercher dans…", QMessageBox.ButtonRole.ActionRole)
        box.setEscapeButton(box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole))
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_locate:
            return self._locate_run_folder(run)
        if clicked is btn_search:
            return self._search_run_folder(run)
        return None

    def _locate_run_folder(self, run: QCRun) -> Path | None:
        old = Path(run.dicom_folder) if run.dicom_folder else None
        start = str(old.parent) if old is not None and old.parent.exists() else ""
        chosen = QFileDialog.getExistingDirectory(self, "Localiser le dossier DICOM du contrôle", start)
        if not chosen:
            return None
        folder = Path(chosen)
        if not folder_matches(folder, run.series_uid):
            QMessageBox.warning(
                self, "Série différente",
                "Ce dossier ne contient pas la série du contrôle sélectionné.\n\n"
                f"Identifiant attendu :\n{run.series_uid}")
            return None
        self._remember_run_folder(run, folder)
        return folder

    def _search_run_folder(self, run: QCRun) -> Path | None:
        root = QFileDialog.getExistingDirectory(self, "Dossier dans lequel rechercher la série", "")
        if not root:
            return None
        from PySide6.QtWidgets import QProgressDialog
        progress = QProgressDialog("Recherche de la série…", "Annuler", 0, 0, self)
        progress.setWindowTitle("Recherche")
        # Modal: the events processed during the walk must not start another load
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(300)
        progress.setValue(0)

        def visit(folder: Path) -> bool:
            progress.setLabelText(f"Recherche de la série…\n{folder}")
            QApplication.processEvents()
            return not progress.wasCanceled()

        found = find_series_folder(Path(root), run.series_uid, progress=visit)
        cancelled = progress.wasCanceled()
        progress.close()
        if found is None:
            if not cancelled:
                QMessageBox.information(
                    self, "Série introuvable",
                    f"Aucun dossier de {root} ne contient la série du contrôle du {run.date_fr()}.")
            return None
        self._remember_run_folder(run, found)
        return found

    def _remember_run_folder(self, run: QCRun, folder: Path):
        """Store the found folder on the run and learn the relocation for the other runs."""
        new_path = str(folder.resolve())
        relocation = relocation_between(run.dicom_folder, new_path)
        run.dicom_folder = new_path
        run.dicom_folder_rel = relative_to_database(new_path, self._device_db.db_path)
        try:
            self._device_db.update_run(self._current_device.device_id, run)
            if relocation is not None:
                self._device_db.add_relocation(*relocation)
        except (OSError, LookupError, ValueError) as e:
            QMessageBox.warning(self, "Historique", f"Impossible d'enregistrer l'emplacement des images :\n{e}")

    def _format_results_html(self) -> str:
        """Format all results as HTML."""
        if self._current_results is None and self._nps_results is None and self._artifact_result is None:
            return self._get_empty_results_html()

        c = _theme_colors()
        css = f"""
        <style>
            body {{ font-family: -apple-system, sans-serif; font-size: 12px; color: {c['text']}; margin: 8px; }}
            .section {{ margin-bottom: 16px; }}
            .section-title {{
                font-weight: bold;
                font-size: 13px;
                color: {c['section_title']};
                border-bottom: 1px solid {c['section_border']};
                padding-bottom: 4px;
                margin-bottom: 8px;
            }}
            table {{ width: 100%; border-collapse: collapse; margin: 4px 0; }}
            th, td {{ padding: 4px 8px; text-align: left; }}
            th {{ color: {c['th']}; font-weight: normal; width: 45%; }}
            td {{ color: {c['td']}; }}
            .ok {{ color: #4caf50; font-weight: bold; }}
            .nc {{ color: #ff9800; font-weight: bold; }}
            .ncg {{ color: #f44336; font-weight: bold; }}
            .pending {{ color: {c['pending']}; font-style: italic; }}
            .value {{ font-family: monospace; }}
            .roi-table th {{ width: 20%; text-align: center; }}
            .roi-table td {{ text-align: center; font-family: monospace; }}
            .roi-header {{ background-color: {c['roi_header_bg']}; }}
        </style>
        """

        html_parts = [css, self._format_phantom_warning_html()]

        # Water phantom results
        if self._current_results:
            html_parts.append(self._format_water_results_html())

        # NPS results
        if self._nps_results:
            html_parts.append(self._format_nps_html())

        # Artifact inspection results
        html_parts.append(self._format_artifact_html())

        return "".join(html_parts)

    def _format_phantom_warning_html(self) -> str:
        """Warning box shown when the ROIs were placed without finding the phantom wall."""
        undetected = self._phantom_warnings()
        if not undetected:
            return ""
        c = _theme_colors()
        items = "".join(f"<li>{w}</li>" for w in undetected)
        return f"""
        <div style="margin-bottom: 12px; padding: 8px; background-color: {c['warning_bg']}; border-radius: 4px; border-left: 3px solid {c['warning_border']};">
            <div style="color: {c['warning_title']}; font-weight: bold; margin-bottom: 4px;">⚠ Fantôme non détecté</div>
            <div style="color: {c['warning_text']}; font-size: 11px;">
                La paroi du fantôme n'a pas été repérée : les ROI sont placées par défaut
                et les mesures ne sont valables que si elles sont dans l'eau.
                Vérifiez leur position sur l'image ou choisissez une autre coupe.
                <ul style="margin: 4px 0 0 0; padding-left: 20px;">{items}</ul>
            </div>
        </div>
        """

    def _format_water_results_html(self) -> str:
        """Format water phantom results as HTML table."""
        r = self._current_results
        if r is None:
            return ""

        # Status formatting
        def status_html(acceptable: bool, ncg: bool = False) -> str:
            if acceptable:
                return '<span class="ok">✓ Conforme</span>'
            elif ncg:
                return '<span class="ncg">✗ NCG</span>'
            else:
                return '<span class="nc">✗ NC</span>'

        ct_status = water_ct_status(r.water_ct_number)
        if ct_status == NC_OR_NCG:
            ct_status_html = f'<span class="ncg">✗ {NC_OR_NCG_NOTICE}</span>'
        else:
            ct_status_html = status_html(ct_status == OK, ct_status == NCG)

        return f"""
        <div class="section">
            <div class="section-title">Nombre CT de l'eau</div>
            <table>
                <tr><th>Valeur centrale</th><td class="value">{format_fr(r.water_ct_number, 1, sign=True)} UH</td></tr>
                <tr><th>Critère</th><td>±7 UH (NCG : ±25 UH)</td></tr>
                <tr><th>Statut</th><td>{ct_status_html}</td></tr>
            </table>
        </div>

        <div class="section">
            <div class="section-title">Uniformité</div>
            <table>
                <tr><th>Écart max C-P</th><td class="value">{format_fr(r.uniformity, 1)} UH</td></tr>
                <tr><th>Critère</th><td>≤ 7 UH</td></tr>
                <tr><th>Statut</th><td>{status_html(r.uniformity_acceptable)}</td></tr>
            </table>
        </div>

        {self._format_noise_html()}

        <div class="section">
            <div class="section-title">Détail par ROI</div>
            <table class="roi-table">
                <tr class="roi-header">
                    <th>ROI</th><th>Moyenne</th><th>Écart-type</th>
                </tr>
                <tr><td>Centre</td><td>{format_fr(r.central.mean_hu, 1, sign=True)}</td><td>{format_fr(r.central.std_hu, 1)}</td></tr>
                <tr><td>12h</td><td>{format_fr(r.top.mean_hu, 1, sign=True)}</td><td>{format_fr(r.top.std_hu, 1)}</td></tr>
                <tr><td>3h</td><td>{format_fr(r.right.mean_hu, 1, sign=True)}</td><td>{format_fr(r.right.std_hu, 1)}</td></tr>
                <tr><td>6h</td><td>{format_fr(r.bottom.mean_hu, 1, sign=True)}</td><td>{format_fr(r.bottom.std_hu, 1)}</td></tr>
                <tr><td>9h</td><td>{format_fr(r.left.mean_hu, 1, sign=True)}</td><td>{format_fr(r.left.std_hu, 1)}</td></tr>
            </table>
            <table>
                {self._format_roi_geometry_html()}
            </table>
        </div>
        """

    def _format_noise_html(self) -> str:
        """Noise section: mean σ of the SPB ROIs over the analysed slices (ANSM 9.1.7.2)."""
        central = ""
        if self._current_results is not None:
            central = (f'<tr><th>Écart-type ROI centrale</th><td class="value">'
                       f'{format_fr(self._current_results.central.std_hu, 2)} UH '
                       f'<span class="pending">(coupe UH, pour information)</span></td></tr>')
        n = self._nps_results
        if n is None:
            rows = ('<tr><th>Bruit (ROI du SPB)</th>'
                    '<td class="pending">Mesuré avec le SPB (non disponible)</td></tr>')
        else:
            rows = (f'<tr><th>Bruit (ROI du SPB)</th><td class="value">{format_fr(n.noise, 2)} UH</td></tr>'
                    f'<tr><th>Mesure</th><td>moyenne des écarts-types de {n.noise_roi_count} ROI '
                    f'({n.num_slices} coupe{"s" if n.num_slices > 1 else ""})</td></tr>'
                    + self._format_noise_stability_html(n.noise))
        return f"""
        <div class="section">
            <div class="section-title">Bruit</div>
            <table>
                {rows}
                {central}
            </table>
        </div>
        """

    def _format_nps_html(self) -> str:
        """Format NPS results as HTML table with 1D plot."""
        r = self._nps_results
        if r is None:
            return ""

        # Generate NPS plot as base64 image
        plot_img = self._generate_nps_plot(r)

        num_rois = len(r.roi_config.rois)

        # Build warnings HTML if any
        warnings_html = ""
        c = _theme_colors()

        # Warning for insufficient slices (ANSM requires 10 slices)
        if r.num_slices < 10:
            warnings_html += f"""
            <div style="margin-top: 8px; padding: 8px; background-color: {c['warning_bg']}; border-radius: 4px; border-left: 3px solid {c['warning_border']};">
                <div style="color: {c['warning_title']}; font-weight: bold; margin-bottom: 4px;">⚠ Nombre de coupes insuffisant</div>
                <div style="color: {c['warning_text']}; font-size: 11px;">
                    L'ANSM recommande d'analyser 10 coupes centrées sur la coupe centrale.
                    Seulement {r.num_slices} coupe(s) utilisée(s).
                </div>
            </div>
            """

        if r.roi_warnings:
            # Limit to first 5 warnings to avoid overwhelming the display
            displayed_warnings = r.roi_warnings[:5]
            warnings_list = "".join(
                f"<li>{w.message}</li>" for w in displayed_warnings
            )
            more_text = ""
            if len(r.roi_warnings) > 5:
                more_text = f"<li><i>... et {len(r.roi_warnings) - 5} autre(s)</i></li>"
            warnings_html += f"""
            <div style="margin-top: 8px; padding: 8px; background-color: {c['warning_bg']}; border-radius: 4px; border-left: 3px solid {c['warning_border']};">
                <div style="color: {c['warning_title']}; font-weight: bold; margin-bottom: 4px;">⚠ Attention : contenu non uniforme détecté</div>
                <ul style="margin: 0; padding-left: 20px; color: {c['warning_text']}; font-size: 11px;">
                    {warnings_list}
                    {more_text}
                </ul>
            </div>
            """

        return f"""
        <div class="section">
            <div class="section-title">Spectre de Puissance du Bruit (SPB)</div>
            <table>
                <tr><th>Coupes analysées</th><td class="value">{r.num_slices}</td></tr>
                <tr><th>Nombre de ROI</th><td class="value">{num_rois}</td></tr>
                <tr><th>Taille ROI</th><td class="value">{r.roi_size} × {r.roi_size} px{" (figée)" if r.geometry is not None and r.geometry.is_frozen else ""}</td></tr>
                <tr><th>Fréquence moyenne</th><td class="value">{format_fr(r.mean_frequency, 3)} cycles/mm</td></tr>
                {self._format_nps_stability_html(r.mean_frequency)}
            </table>
            {warnings_html}
            <div style="margin-top: 12px; text-align: center;">
                <img src="data:image/png;base64,{plot_img}" style="max-width: 100%; border-radius: 4px;"/>
            </div>
        </div>
        """

    def _generate_nps_plot(self, nps_result: NPSResult) -> str:
        """Generate 1D NPS plot and return as base64-encoded PNG."""
        # Lazy import matplotlib to improve startup time
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(5, 3), dpi=100)
        try:

            # Dark theme styling
            fig.patch.set_facecolor('#2b2b2b')
            ax.set_facecolor('#1e1e1e')

            # Plot radial NPS
            ax.plot(
                nps_result.frequencies_radial,
                nps_result.nps_radial,
                color='#4fc3f7',
                linewidth=1.5,
                label='SPB radial'
            )

            # Mark the mean frequency
            ax.axvline(
                x=nps_result.mean_frequency,
                color='#ff9800',
                linestyle='--',
                linewidth=1,
                alpha=0.7,
                label=f'f_moy: {format_fr(nps_result.mean_frequency, 2)} c/mm'
            )

            # Styling
            ax.set_xlabel('Fréquence (cycles/mm)', color='#aaa', fontsize=9)
            ax.set_ylabel('SPB (UH²·mm²)', color='#aaa', fontsize=9)
            ax.tick_params(colors='#888', labelsize=8)
            ax.spines['bottom'].set_color('#555')
            ax.spines['left'].set_color('#555')
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.grid(True, alpha=0.2, color='#555')
            ax.legend(loc='upper right', fontsize=8, facecolor='#333', edgecolor='#555', labelcolor='#ccc')

            # Set axis limits (x to Nyquist, y from 0)
            nyquist = 1.0 / (2.0 * nps_result.pixel_size_mm)
            ax.set_xlim(0, nyquist)
            ax.set_ylim(0, None)

            plt.tight_layout()

            # Save to base64
            buffer = BytesIO()
            fig.savefig(buffer, format='png', facecolor=fig.get_facecolor(), edgecolor='none')
            buffer.seek(0)
        finally:
            plt.close(fig)

        return base64.b64encode(buffer.read()).decode('utf-8')

    def _format_artifact_html(self) -> str:
        """Format artifact inspection results as HTML."""
        if self._artifact_result is None:
            # Not yet inspected
            return """
        <div class="section">
            <div class="section-title">Artéfacts</div>
            <table>
                <tr><th>Inspection visuelle</th><td class="pending">Non effectuée</td></tr>
                <tr><th>Critère</th><td>Aucun artéfact cliniquement gênant</td></tr>
            </table>
        </div>
        """

        # Artifact result available
        if self._artifact_result:
            # Artifacts present = NC
            status_html = '<span class="nc">✗ NC - Présence d\'artéfacts</span>'
            # Include description if provided
            desc_row = ""
            if self._artifact_description:
                # Escape HTML in description
                escaped_desc = html.escape(self._artifact_description).replace('\n', '<br/>')
                desc_row = f'<tr><th>Description</th><td>{escaped_desc}</td></tr>'
        else:
            # No artifacts = Conforme
            status_html = '<span class="ok">✓ Conforme - Absence d\'artéfacts</span>'
            desc_row = ""

        return f"""
        <div class="section">
            <div class="section-title">Artéfacts</div>
            <table>
                <tr><th>Inspection visuelle</th><td>{status_html}</td></tr>
                <tr><th>Critère</th><td>Aucun artéfact cliniquement gênant</td></tr>
                <tr><th>Fenêtre utilisée</th><td class="value">largeur 80 UH, centre 0 UH</td></tr>
                {desc_row}
            </table>
        </div>
        """

    def _format_noise_stability_html(self, noise: float) -> str:
        """Format noise stability comparison with reference value."""
        # Read reference value from text field directly (works even without a saved device)
        ref_noise_text = self._edit_ref_noise.text().strip()
        ref_noise = parse_float_fr(ref_noise_text) if ref_noise_text else None
        if ref_noise is None:
            return """
                <tr><th>Critère stabilité</th><td>MIN(-0,2 ; -0,1×σ<sub>réf</sub>) ≤ écart ≤ MAX(0,2 ; 0,1×σ<sub>réf</sub>)</td></tr>
                <tr><th>Statut</th><td class="pending">Renseigner σ de référence</td></tr>
        """
        deviation = noise - ref_noise

        # ANSM criterion: MIN(-0.2, -0.1*B_ref) ≤ (B_i - B_ref) ≤ MAX(0.2, 0.1*B_ref)
        # (same evaluation as the history and the PDF)
        lower_bound, upper_bound = noise_bounds(ref_noise)
        is_conforme = noise_status(noise, ref_noise) == OK

        if is_conforme:
            status_html = '<span class="ok">✓ Conforme</span>'
        else:
            status_html = '<span class="nc">✗ Non conforme</span>'

        return f"""
                <tr><th>Valeur de référence</th><td class="value">{format_fr(ref_noise, 2)} UH</td></tr>
                <tr><th>Écart</th><td class="value">{format_fr(deviation, 2, sign=True)} UH</td></tr>
                <tr><th>Critère</th><td>[{format_fr(lower_bound, 2, sign=True)}, {format_fr(upper_bound, 2, sign=True)}] UH</td></tr>
                <tr><th>Statut</th><td>{status_html}</td></tr>
        """

    def _format_nps_stability_html(self, mean_freq: float) -> str:
        """Format NPS frequency stability comparison with reference value."""
        # Read reference value from text field directly (works even without a saved device)
        ref_nps_text = self._edit_ref_nps_freq.text().strip()
        ref_freq = parse_float_fr(ref_nps_text) if ref_nps_text else None
        if ref_freq is None or ref_freq == 0:
            return """
                <tr><th>Critère stabilité</th><td>±10 %</td></tr>
                <tr><th>Statut</th><td class="pending">Renseigner fréquence de référence</td></tr>
        """

        deviation_pct = ((mean_freq - ref_freq) / ref_freq) * 100
        is_conforme = nps_status(mean_freq, ref_freq) == OK

        if is_conforme:
            status_html = '<span class="ok">✓ Conforme</span>'
        else:
            status_html = '<span class="nc">✗ Non conforme</span>'

        return f"""
                <tr><th>Fréquence de référence</th><td class="value">{format_fr(ref_freq, 3)} cycles/mm</td></tr>
                <tr><th>Écart</th><td class="value">{format_fr(deviation_pct, 1, sign=True)} %</td></tr>
                <tr><th>Critère</th><td>±10 %</td></tr>
                <tr><th>Statut</th><td>{status_html}</td></tr>
        """

    # ===== Device Database Methods =====

    def _refresh_device_combo(self):
        """Refresh the device dropdown with saved devices."""
        # Block signals to avoid triggering selection change
        self._device_combo.blockSignals(True)

        # Remember current selection
        current_id = None
        if self._current_device:
            current_id = self._current_device.device_id

        # Clear and rebuild
        self._device_combo.clear()
        self._device_combo.addItem(self._placeholder_install_label(), None)

        # Saved installations stay upright even when the placeholder, and with
        # it the closed combo, is drawn in italic
        upright = QFont(self._device_combo.font())
        upright.setItalic(False)

        devices = self._device_db.get_all_devices()
        selected_index = 0
        for i, device in enumerate(devices):
            self._device_combo.addItem(device.display_name(), device.device_id)
            self._device_combo.setItemData(i + 1, upright, Qt.ItemDataRole.FontRole)
            if current_id and device.device_id == current_id:
                selected_index = i + 1  # +1 for the placeholder item

        self._device_combo.setCurrentIndex(selected_index)
        self._device_combo.blockSignals(False)
        self._update_install_selector()

    def _show_device_hu_slice(self, device: DeviceConfig | None):
        """Display the HU slice saved for `device`, if there is one to show.

        The HU slice is the one an installation is controlled on, so it is a
        better landing point than the middle of the series, both when a scanner
        is recognised on load and when another installation is picked by hand.
        """
        if device is None or device.hu_slice_index is None or self._current_series is None:
            return
        hu_slice = self.image_viewer.get_hu_slice_index()
        if 0 <= hu_slice < self._current_series.num_images:
            self.image_viewer.set_current_slice(hu_slice)
            self._on_slice_changed(hu_slice)

    def _on_device_selected(self, index: int):
        """Handle device selection from dropdown."""
        device_id = self._device_combo.itemData(index)
        if device_id is None:
            # Placeholder entry selected - reset to defaults
            self._current_device = None
            self._load_device_config(None)
        else:
            device = self._device_db.get_device(device_id)
            if device:
                self._current_device = device
                self._load_device_config(device)
                self._show_device_hu_slice(device)
        self._update_install_summary()

    def _edit_installation(self):
        """"Modifier…" / "Nouvelle installation": open the installations window."""
        self._show_device_manager()

    def _slice_selection_state(self) -> tuple[str, str | None, bool]:
        """Return (current selection text, saved selection text or None, differs)."""
        if self._current_series is None:
            return "", None, False
        hu = self.image_viewer.get_hu_slice_index()
        start, end = self.image_viewer.get_nps_slice_range()
        current = f"UH {hu + 1} · SPB {start + 1}–{end + 1}"
        if self._current_device is None or self._saved_hu_slice is None \
                or self._saved_nps_start is None or self._saved_nps_end is None:
            return current, None, False
        saved = f"UH {self._saved_hu_slice + 1} · SPB {self._saved_nps_start + 1}–{self._saved_nps_end + 1}"
        return current, saved, current != saved

    def _placeholder_install_label(self) -> str:
        """Text of the first combo entry, which stands for "no installation"."""
        if getattr(self, "_current_image", None) is None:
            return NO_INSTALL_LABEL
        return UNKNOWN_INSTALL_LABEL

    def _update_install_selector(self):
        """Label and highlight the selector according to the current state.

        Three states are distinguished:
        - no series loaded: "Aucune installation sélectionnée", plain button;
        - series loaded but not matched to a saved installation:
          "Installation inconnue", highlighted "Nouvelle installation" button,
          because recording one is the next thing to do;
        - installation selected: its name, plain "Modifier…" button.

        The placeholder is drawn in italic so it does not read as the name of a
        saved installation.
        """
        if not hasattr(self, "_device_combo"):
            return
        placeholder = self._current_device is None

        if placeholder:
            label = self._placeholder_install_label()
            self._device_combo.setItemText(0, label)
            italic = QFont(self._device_combo.font())
            italic.setItalic(True)
            self._device_combo.setItemData(0, italic, Qt.ItemDataRole.FontRole)

        # The closed combo ignores the item font role, so the widget font
        # carries the italic; saved entries get an upright font of their own.
        font = QFont(self._device_combo.font())
        font.setItalic(placeholder)
        self._device_combo.setFont(font)

        if not hasattr(self, "_btn_edit_device"):
            return
        if not placeholder:
            self._btn_edit_device.setText(EDIT_INSTALL_LABEL)
            self._btn_edit_device.setToolTip(
                "Renseigner l'installation et ses valeurs de référence")
            self._btn_edit_device.setStyleSheet("")
            return

        self._btn_edit_device.setText(NEW_INSTALL_LABEL)
        self._btn_edit_device.setToolTip(
            "Enregistrer cette installation et ses valeurs de référence")
        if self._current_image is None:
            # Nothing to record yet: keep the button quiet
            self._btn_edit_device.setStyleSheet("")
            return
        self._btn_edit_device.setStyleSheet(primary_button_style())

    def _update_install_summary(self):
        """Refresh the one-glance summary shown under the device selector."""
        self._update_install_selector()
        if not hasattr(self, "_install_summary"):
            return
        c = _theme_colors()
        if self._current_device is None:
            saved = "Installation non enregistrée"
        else:
            parts = [p for p in (self._edit_hospital_name.text().strip(),
                                 self._edit_hospital_location.text().strip()) if p]
            serial = self._edit_serial_number.text().strip()
            if serial:
                parts.append(f"n° série {serial}")
            # The label is rich text: "A&B" or "<2" typed by the user stay as typed
            saved = html.escape(" · ".join(parts)) if parts else "Aucune information renseignée"

        noise_text = self._edit_ref_noise.text().strip()
        nps_text = self._edit_ref_nps_freq.text().strip()
        ref_noise = parse_float_fr(noise_text) if noise_text else None
        ref_nps = parse_float_fr(nps_text) if nps_text else None
        refs = []
        if ref_noise is not None:
            refs.append(f"σ réf. {format_fr(ref_noise, 2)} UH")
        if ref_nps is not None:
            refs.append(f"f SPB réf. {format_fr(ref_nps, 3)} c/mm")
        if refs:
            ref_line = " · ".join(refs)
        elif self._current_results is not None and self._nps_results is not None:
            # Offer the one-click first-control shortcut where the gap is noticed
            ref_line = (f'<span style="color:{c["warning_border"]};">Valeurs de référence non '
                        f'renseignées</span> — <a href="set-reference" style="color:{c["accent"]};">'
                        "définir les valeurs actuelles comme références</a>")
        else:
            ref_line = f'<span style="color:{c["warning_border"]};">Valeurs de référence non renseignées</span>'

        modified = ""
        current, saved_slices, differs = self._slice_selection_state()
        save_link = f'<a href="save-slices" style="color:{c["accent"]};">mémoriser ces coupes</a>'
        if self._saved_slices_mismatch is not None and current:
            saved_length, length = self._saved_slices_mismatch
            slice_line = (f'<br>Coupes {current} — <span style="color:{c["warning_border"]};">les coupes '
                          f"mémorisées n'ont pas été appliquées : elles ont été choisies sur une série de "
                          f'{saved_length} coupes, celle-ci en compte {length}</span> — {save_link}')
        elif differs:
            slice_line = (f'<br><span style="color:{c["warning_border"]};">Coupes {current} ≠ enregistrées '
                          f'({saved_slices})</span> — {save_link}')
        elif current and saved_slices is None and self._current_device is not None:
            slice_line = f"<br>Coupes {current} · <i>non enregistrées</i> — {save_link}"
        elif current:
            slice_line = f"<br>Coupes {current}"
        else:
            slice_line = ""
        date_line = ""
        if self._current_series is not None:
            control_date, manual = self._control_date()
            date_link = f'<a href="set-date" style="color:{c["accent"]};">'
            if not control_date:
                date_line = (f'<br><span style="color:{c["warning_border"]};">Date du contrôle absente '
                             f'des images</span> — {date_link}saisir la date</a>')
            elif manual:
                date_line = (f"<br>Contrôle du {iso_to_fr(control_date)} (date saisie manuellement) — "
                             f"{date_link}modifier</a>")
        self._install_summary.setText(f"{saved}<br>{ref_line}{modified}{slice_line}{date_line}")

    def _load_device_config(self, device: DeviceConfig | None, apply_slices: bool = True):
        """Load device configuration into the UI fields.

        `apply_slices` moves the viewer to the slices saved for the device; it
        is off when the device is merely redrawn after a database refresh.
        """
        self._refresh_history(device)
        if device is not None:
            self._remember_device(device)
        if device is None:
            # Clear fields (placeholders will show in grey)
            self._edit_hospital_name.clear()
            self._edit_hospital_location.clear()
            self._edit_device_name.clear()
            self._edit_commissioning_date.clear()
            self._edit_serial_number.clear()
            self._edit_inventory_number.clear()
            self._edit_ref_noise.clear()
            self._edit_ref_nps_freq.clear()
            # Clear saved slice values
            self._saved_hu_slice = None
            self._saved_nps_start = None
            self._saved_nps_end = None
            self._saved_slices_mismatch = None
        else:
            # Load device values (skip placeholder-like values from old configs)
            def load_value(edit, value):
                if value and not str(value).startswith("["):
                    edit.setText(str(value))
                else:
                    edit.clear()
            load_value(self._edit_hospital_name, device.hospital_name)
            load_value(self._edit_hospital_location, device.hospital_location)
            load_value(self._edit_device_name, device.device_name)
            load_value(self._edit_commissioning_date, device.commissioning_date)
            load_value(self._edit_serial_number, device.serial_number)
            load_value(self._edit_inventory_number, device.inventory_number)
            # Reference values
            if device.reference_noise is not None:
                self._edit_ref_noise.setText(format_fr(device.reference_noise, 2))
            else:
                self._edit_ref_noise.clear()
            if device.reference_nps_freq is not None:
                self._edit_ref_nps_freq.setText(format_fr(device.reference_nps_freq, 3))
            else:
                self._edit_ref_nps_freq.clear()
            # Store saved slice values for restoration
            self._saved_hu_slice = device.hu_slice_index
            self._saved_nps_start = device.nps_start_slice
            self._saved_nps_end = device.nps_end_slice
            self._saved_slices_mismatch = None
            saved_length = device.slices_series_length
            if self._current_series is not None and saved_length is not None \
                    and saved_length != self._current_series.num_images \
                    and device.hu_slice_index is not None:
                # Slice numbers chosen on a series of another length do not
                # designate the same place in this one (the decision asks for
                # the central slice): they are not applied, and the summary says so
                self._saved_slices_mismatch = (saved_length, self._current_series.num_images)
                self._saved_hu_slice = self._saved_nps_start = self._saved_nps_end = None
            # Apply slice values if they exist and we have a series loaded
            elif self._current_series is not None:
                # A slice saved for a longer series can never be reached here: compare
                # against what the viewer can actually show, or the "modified" state
                # and the reset button would stay on for good
                last = self._current_series.num_images - 1
                if self._saved_hu_slice is not None:
                    self._saved_hu_slice = max(0, min(self._saved_hu_slice, last))
                if self._saved_nps_start is not None and self._saved_nps_end is not None:
                    lo = max(0, min(self._saved_nps_start, last))
                    hi = max(0, min(self._saved_nps_end, last))
                    self._saved_nps_start, self._saved_nps_end = min(lo, hi), max(lo, hi)
                if apply_slices and device.hu_slice_index is not None:
                    self.image_viewer.set_hu_slice_index(device.hu_slice_index)
                if apply_slices and device.nps_start_slice is not None and device.nps_end_slice is not None:
                    self.image_viewer.set_nps_slice_range(device.nps_start_slice, device.nps_end_slice)

    def _try_auto_detect_device(self):
        """Try to auto-detect device from database based on DICOM metadata."""
        if self._current_image is None:
            return

        img = self._current_image
        device = self._device_db.find_device(
            img.manufacturer or "",
            img.model_name or "",
            img.station_name or "",
            img.device_serial_number or "",
        )

        if device:
            # Device found in database - load it
            self._current_device = device
            self._load_device_config(device)
            self._refresh_device_combo()
            self._update_install_summary()
            self._show_device_hu_slice(device)
            self.statusbar.showMessage(
                f"Installation reconnue : {device.display_name()}"
            )
        else:
            # Device not found: clear everything that belonged to the previous
            # device (references, identity, history) so the new scanner is not
            # judged against, or reported with, another installation's values
            self._current_device = None
            self._load_device_config(None)
            # Set combo to "Installation inconnue"
            self._device_combo.blockSignals(True)
            self._device_combo.setCurrentIndex(0)
            self._device_combo.blockSignals(False)
            self._update_install_summary()

    def _show_device_manager(self):
        """Open the installations window, then take its changes into account."""
        # Edit the installations as they are now, not as they were at startup
        self._refresh_database(force=True)
        current_slices = None
        if self._current_series is not None:
            start, end = self.image_viewer.get_nps_slice_range()
            current_slices = (self.image_viewer.get_hu_slice_index(), start, end)
        # An image without DICOM identity belongs to the installation selected by hand
        image_device_id = None
        if self._current_image is not None and not self._has_dicom_identity(self._current_image) \
                and self._current_device is not None:
            image_device_id = self._current_device.device_id
        dialog = DeviceManagerDialog(
            self._device_db, self,
            select_device_id=self._current_device.device_id if self._current_device else None,
            current_image=self._current_image,
            current_slices=current_slices,
            current_series_length=(self._current_series.num_images
                                   if self._current_series is not None else None),
            current_analysis=self._current_analysis_for_reference(),
            image_device_id=image_device_id)
        dialog.exec()
        self._after_device_manager(dialog)

    def _current_analysis_for_reference(self) -> tuple[float, float | None, ROIGeometry | None] | None:
        """(noise, SPB frequency, frozen ROI geometry) of the analysis on screen, None if none."""
        if self._current_results is None or self._nps_results is None:
            return None  # noise and frequency both come from the SPB ROIs
        if self._phantom_warnings():
            return None  # ROIs placed blind: not a reference
        return (self._nps_results.noise, self._nps_results.mean_frequency,
                self._run_geometry(self._current_run_for_history()))

    def _after_device_manager(self, dialog: DeviceManagerDialog):
        """Resync the window with the database the installations window left."""
        # The database file itself may have been switched in the dialog
        self._device_db = dialog.device_db
        # The dialog may have edited, deleted or replaced the device shown here:
        # resolve it again by id so the results panel, the PDF and the history
        # never use a stale object. A loaded image wins: its installation may
        # have just been created in the dialog.
        img = self._current_image
        if img is not None and self._has_dicom_identity(img):
            device = self._device_db.find_device(img.manufacturer or "", img.model_name or "",
                                                 img.station_name or "", img.device_serial_number or "")
        elif img is not None:
            # No scanner identity: the installation is the one attached by hand,
            # in the dialog or before it
            attached = dialog.image_device_id
            device = self._device_db.get_device(attached) if attached else None
        else:
            current_id = self._current_device.device_id if self._current_device else None
            device = self._device_db.get_device(current_id) if current_id else None
            if device is None and dialog.selected_device_id:
                device = self._device_db.get_device(dialog.selected_device_id)
            if device is None:
                device = self._default_device()
        self._current_device = device
        self._load_device_config(device)
        # References defined in the dialog froze the geometry of the analysis
        # on screen: show it as frozen without waiting for the next analysis
        frozen = device.roi_geometry if device is not None else None
        for result in (self._current_results, self._nps_results):
            if result is None or result.geometry is None:
                continue
            if frozen is None:
                # Geometry reset in the dialog (or no installation any more)
                result.geometry = result.geometry.frozen("")
            elif result.geometry.frozen(frozen.frozen_date) == frozen:
                result.geometry = frozen
        self._refresh_device_combo()
        self._update_results_display()
        self._update_install_summary()
