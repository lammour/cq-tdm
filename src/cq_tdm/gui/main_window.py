"""Main application window."""

import base64
from io import BytesIO
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
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
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
    QTabWidget,
)

# Lightweight imports - no heavy dependencies
from ..core.app_config import get_app_config, save_app_config
from ..core.device_database import DeviceConfig, DeviceDatabase
from ..core.utils import format_fr, parse_float_fr
from ..core.qc_history import OK, QCRun, dicom_date_to_iso, noise_bounds, noise_status, nps_status
from ..core.dicom_locator import (
    find_series_folder, folder_matches, relative_to_database, relocation_between, resolve_dicom_folder,
)
from .history_panel import HistoryPanel

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
    detect_phantom_center,
    estimate_phantom_diameter,
)

# Report imports - deferred via lazy __init__.py (reportlab)
from ..reports import generate_pdf_report, generate_report_filename, ArtifactInspectionResult


def _theme_colors() -> dict:
    """Return color dict based on current theme setting."""
    is_light = get_app_config().theme == "light"
    if is_light:
        return {
            "bg": "#ffffff",
            "text": "#222222",
            "text_secondary": "#555555",
            "border": "#cccccc",
            "section_title": "#1565c0",
            "section_border": "#1565c0",
            "th": "#666666",
            "td": "#222222",
            "roi_header_bg": "#e0e0e0",
            "pending": "#999999",
            "warning_bg": "#fff3e0",
            "warning_border": "#ff9800",
            "warning_title": "#e65100",
            "warning_text": "#bf360c",
        }
    else:
        return {
            "bg": "#2b2b2b",
            "text": "#e0e0e0",
            "text_secondary": "#aaaaaa",
            "border": "#444444",
            "section_title": "#4fc3f7",
            "section_border": "#4fc3f7",
            "th": "#aaaaaa",
            "td": "#ffffff",
            "roi_header_bg": "#333333",
            "pending": "#888888",
            "warning_bg": "#4a3000",
            "warning_border": "#ff9800",
            "warning_title": "#ff9800",
            "warning_text": "#ffcc80",
        }
from .image_viewer import ImageViewerWidget, ROI, ArtifactInspectionDialog


class DeviceManagerDialog(QDialog):
    """Dialog for managing saved CT installations."""

    def __init__(self, device_db: DeviceDatabase, parent=None):
        super().__init__(parent)
        self.device_db = device_db
        self.setWindowTitle("Gestion des installations")
        self.setMinimumSize(750, 550)
        self._setup_ui()
        self._refresh_device_list()

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

        btn_select_db = QPushButton("Ouvrir...")
        btn_select_db.clicked.connect(self._select_database)
        db_buttons_layout.addWidget(btn_select_db)

        btn_new_db = QPushButton("Nouvelle...")
        btn_new_db.clicked.connect(self._create_new_database)
        db_buttons_layout.addWidget(btn_new_db)

        btn_move_db = QPushButton("Déplacer...")
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

        # Delete button
        self._btn_delete = QPushButton("Supprimer l'installation")
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._delete_selected_device)
        left_layout.addWidget(self._btn_delete)

        layout.addWidget(left_panel, 1)

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

        self._edit_ref_noise = QLineEdit()
        self._edit_ref_noise.setPlaceholderText("Bruit de référence (HU)")
        form_layout.addRow("Bruit réf. (σ) :", self._edit_ref_noise)

        self._edit_ref_nps_freq = QLineEdit()
        self._edit_ref_nps_freq.setPlaceholderText("Fréquence moyenne SPB (cycles/mm)")
        form_layout.addRow("Fréq. SPB réf. :", self._edit_ref_nps_freq)

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

        right_layout.addWidget(form)
        right_layout.addStretch()

        # Save button
        self._btn_save = QPushButton("Enregistrer les modifications")
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._save_current_device)
        right_layout.addWidget(self._btn_save)

        layout.addWidget(right_panel, 1)

        # Store current device
        self._current_device: DeviceConfig | None = None

    def _refresh_device_list(self):
        """Refresh the device list."""
        self._device_list.clear()
        devices = self.device_db.get_all_devices()

        for device in devices:
            item = QListWidgetItem(device.display_name())
            item.setData(Qt.ItemDataRole.UserRole, device.device_id)
            self._device_list.addItem(item)

        if not devices:
            self._clear_form()

    def _on_device_selected(self, current: QListWidgetItem, previous: QListWidgetItem):
        """Handle device selection."""
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

        # Slice positions (displayed as 1-based for user)
        if device.hu_slice_index is not None:
            self._label_hu_slice.setText(str(device.hu_slice_index + 1))
        else:
            self._label_hu_slice.setText("—")
        if device.nps_start_slice is not None and device.nps_end_slice is not None:
            self._label_nps_range.setText(f"{device.nps_start_slice + 1} - {device.nps_end_slice + 1}")
        else:
            self._label_nps_range.setText("—")

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
        self._label_manufacturer.setText("—")
        self._label_model.setText("—")
        self._label_station.setText("—")
        self._label_hu_slice.setText("—")
        self._label_nps_range.setText("—")
        self._btn_delete.setEnabled(False)
        self._btn_save.setEnabled(False)

    def _save_current_device(self):
        """Save modifications to the current device."""
        if self._current_device is None:
            return

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

        # Save to database
        self.device_db.save_device(self._current_device)

        # Refresh list to show updated name
        current_row = self._device_list.currentRow()
        self._refresh_device_list()
        if current_row >= 0 and current_row < self._device_list.count():
            self._device_list.setCurrentRow(current_row)

        QMessageBox.information(
            self,
            "Enregistré",
            f"Installation '{self._current_device.display_name()}' enregistrée."
        )

    def _delete_selected_device(self):
        """Delete the selected device."""
        if self._current_device is None:
            return

        reply = QMessageBox.question(
            self,
            "Confirmer la suppression",
            f"Voulez-vous vraiment supprimer l'installation '{self._current_device.display_name()}' ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.device_db.delete_device(self._current_device.device_id)
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
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Ouvrir une base de données",
            "",
            "JSON Files (*.json);;Tous les fichiers (*)"
        )
        if file_path:
            self._switch_database(file_path)

    def _create_new_database(self):
        """Create a new database file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Créer une nouvelle base de données",
            "devices.json",
            "JSON Files (*.json)"
        )
        if file_path:
            # Create empty database file
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            Path(file_path).write_text('{"version": 2, "devices": []}', encoding="utf-8")
            self._switch_database(file_path)

    def _move_database(self):
        """Move the current database to a new location."""
        import shutil

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
            "JSON Files (*.json)"
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
            reply = QMessageBox.question(
                self,
                "Supprimer l'ancienne base",
                f"La base de données a été copiée vers :\n{new_path}\n\n"
                f"Voulez-vous supprimer l'ancienne base de données ?\n{old_path}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )

            if reply == QMessageBox.StandardButton.Yes:
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
                "Erreur",
                f"Impossible de déplacer la base de données :\n{e}"
            )

    def _switch_database(self, file_path: str):
        """Switch to a different database file."""
        config = get_app_config()
        config.device_database_path = file_path
        save_app_config()

        # Reload device database
        self.device_db = DeviceDatabase(Path(file_path))
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

        btn_select = QPushButton("Parcourir...")
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

        layout.addStretch()

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
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
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
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
        self._debug_mode: bool = False  # Debug mode for phantom detection visualization

        # Device database and current device (use custom path from config if set)
        config = get_app_config()
        db_path = Path(config.device_database_path) if config.device_database_path else None
        self._device_db = DeviceDatabase(db_path)
        self._current_device: DeviceConfig | None = None
        if self._device_db.load_error:
            QTimer.singleShot(0, self._warn_device_db_load_error)

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

        # Saved field values for tracking modifications (to highlight save button)
        self._saved_hospital_name: str = ""
        self._saved_hospital_location: str = ""
        self._saved_device_name: str = ""
        self._saved_commissioning_date: str = ""
        self._saved_serial_number: str = ""
        self._saved_inventory_number: str = ""
        self._saved_ref_noise: str = ""
        self._saved_ref_nps_freq: str = ""

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._setup_shortcuts()

    def _setup_menu(self):
        """Set up the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&Fichier")
        file_menu.addAction("Ouvrir un dossier DICOM...", self._open_dicom_folder, "Ctrl+O")
        file_menu.addSeparator()
        file_menu.addAction("Exporter rapport PDF...", self._export_pdf, "Ctrl+E")
        file_menu.addAction("Exporter positions ROIs SPB (JSON)...", self._export_nps_rois_json)
        file_menu.addAction("Exporter positions ROIs UH (JSON)...", self._export_hu_rois_json)
        file_menu.addSeparator()
        file_menu.addAction("Quitter", self.close, "Ctrl+Q")

        # View menu
        view_menu = menubar.addMenu("&Affichage")
        view_menu.addAction("Informations image...", self._show_image_info, "Ctrl+I")
        view_menu.addSeparator()
        self._theme_action = view_menu.addAction("Thème clair")
        self._theme_action.setCheckable(True)
        self._theme_action.setChecked(get_app_config().theme == "light")
        self._theme_action.triggered.connect(self._toggle_theme)

        # Configuration menu
        config_menu = menubar.addMenu("&Configuration")
        config_menu.addAction("Gestion des installations...", self._show_device_manager)
        config_menu.addAction("Rapports...", self._show_report_settings)

        # Help menu
        help_menu = menubar.addMenu("&Aide")
        help_menu.addAction("Aide", self._show_help, "F1")
        help_menu.addAction("Raccourcis clavier", self._show_shortcuts)
        help_menu.addSeparator()
        self._debug_action = help_menu.addAction("Mode debug (centre/périmètre fantôme)")
        self._debug_action.setCheckable(True)
        self._debug_action.setChecked(False)
        self._debug_action.triggered.connect(self._toggle_debug_mode)
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
        self._device_combo.addItem("-- Nouvelle installation --", None)
        self._refresh_device_combo()
        self._device_combo.currentIndexChanged.connect(self._on_device_selected)
        device_selector_layout.addWidget(self._device_combo, 1)

        # The installation form lives in a dialog, opened from here
        self._btn_edit_device = QPushButton("Modifier…")
        self._btn_edit_device.setToolTip("Renseigner l'installation et ses valeurs de référence")
        self._btn_edit_device.clicked.connect(self._edit_installation)
        device_selector_layout.addWidget(self._btn_edit_device)

        right_layout.addLayout(device_selector_layout)

        self._install_summary = QLabel()
        self._install_summary.setWordWrap(True)
        self._install_summary.setStyleSheet("color: #888; font-size: 11px; margin-left: 2px;")
        right_layout.addWidget(self._install_summary)

        device_form = QWidget()
        device_form_layout = QGridLayout(device_form)
        device_form_layout.setContentsMargins(0, 4, 0, 0)
        device_form_layout.setVerticalSpacing(4)
        device_form_layout.setHorizontalSpacing(8)

        # Editable fields for installation info (with grey placeholders)
        self._edit_hospital_name = QLineEdit()
        self._edit_hospital_name.setPlaceholderText("Nom de l'établissement")
        self._edit_hospital_location = QLineEdit()
        self._edit_hospital_location.setPlaceholderText("Localisation de l'installation")
        self._edit_device_name = QLineEdit()
        self._edit_device_name.setPlaceholderText("Nom de l'installation")
        self._edit_commissioning_date = QLineEdit()
        self._edit_commissioning_date.setPlaceholderText("Date de mise en service")
        self._edit_serial_number = QLineEdit()
        self._edit_serial_number.setPlaceholderText("Numéro de série")
        self._edit_inventory_number = QLineEdit()
        self._edit_inventory_number.setPlaceholderText("Numéro d'inventaire")

        # Reference values for stability tests
        from PySide6.QtGui import QDoubleValidator
        ref_validator = QDoubleValidator()
        ref_validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self._edit_ref_noise = QLineEdit()
        self._edit_ref_noise.setPlaceholderText("Bruit de référence (HU)")
        self._edit_ref_noise.setValidator(ref_validator)
        self._edit_ref_nps_freq = QLineEdit()
        self._edit_ref_nps_freq.setPlaceholderText("Fréq. moyenne SPB réf. (cycles/mm)")
        self._edit_ref_nps_freq.setValidator(ref_validator)

        # Connect signals to update instance variables and check for modifications
        self._edit_hospital_name.textChanged.connect(self._on_field_changed)
        self._edit_hospital_location.textChanged.connect(self._on_field_changed)
        self._edit_device_name.textChanged.connect(self._on_field_changed)
        self._edit_commissioning_date.textChanged.connect(self._on_field_changed)
        self._edit_serial_number.textChanged.connect(self._on_field_changed)
        self._edit_inventory_number.textChanged.connect(self._on_field_changed)
        self._edit_ref_noise.textChanged.connect(self._on_field_changed)
        self._edit_ref_nps_freq.textChanged.connect(self._on_field_changed)

        # Connect reference fields to debounced comparison update
        self._edit_ref_noise.textChanged.connect(self._on_reference_field_changed)
        self._edit_ref_nps_freq.textChanged.connect(self._on_reference_field_changed)

        device_form_layout.addWidget(QLabel("Établissement :"), 0, 0)
        device_form_layout.addWidget(self._edit_hospital_name, 0, 1)
        device_form_layout.addWidget(QLabel("Localisation :"), 1, 0)
        device_form_layout.addWidget(self._edit_hospital_location, 1, 1)
        device_form_layout.addWidget(QLabel("Installation :"), 2, 0)
        device_form_layout.addWidget(self._edit_device_name, 2, 1)
        device_form_layout.addWidget(QLabel("Mise en service :"), 3, 0)
        device_form_layout.addWidget(self._edit_commissioning_date, 3, 1)
        device_form_layout.addWidget(QLabel("N° série :"), 4, 0)
        device_form_layout.addWidget(self._edit_serial_number, 4, 1)
        device_form_layout.addWidget(QLabel("N° inventaire :"), 5, 0)
        device_form_layout.addWidget(self._edit_inventory_number, 5, 1)

        # Reference values section
        ref_label = QLabel("Valeurs de référence :")
        ref_label.setStyleSheet("font-style: italic; color: #888; margin-top: 4px;")
        device_form_layout.addWidget(ref_label, 6, 0, 1, 2)
        device_form_layout.addWidget(QLabel("Bruit réf. (σ) :"), 7, 0)
        device_form_layout.addWidget(self._edit_ref_noise, 7, 1)
        device_form_layout.addWidget(QLabel("Fréq. SPB réf. :"), 8, 0)
        device_form_layout.addWidget(self._edit_ref_nps_freq, 8, 1)

        # Slice selection (read-only here: it is chosen in the main window, saved with the device)
        slices_label = QLabel("Coupes analysées :")
        slices_label.setStyleSheet("font-style: italic; color: #888; margin-top: 4px;")
        device_form_layout.addWidget(slices_label, 9, 0, 1, 2)
        device_form_layout.addWidget(QLabel("Coupe UH :"), 10, 0)
        self._label_dialog_hu_slice = QLabel("—")
        device_form_layout.addWidget(self._label_dialog_hu_slice, 10, 1)
        device_form_layout.addWidget(QLabel("Coupes SPB :"), 11, 0)
        self._label_dialog_nps_range = QLabel("—")
        device_form_layout.addWidget(self._label_dialog_nps_range, 11, 1)
        self._label_dialog_slices_note = QLabel()
        self._label_dialog_slices_note.setWordWrap(True)
        self._label_dialog_slices_note.setStyleSheet("font-size: 11px;")
        device_form_layout.addWidget(self._label_dialog_slices_note, 12, 0, 1, 2)

        # Installation dialog (persistent: the QLineEdits above must outlive each opening
        # because the results view, PDF export and auto-detection read them directly)
        self._install_dialog = QDialog(self)
        self._install_dialog.setWindowTitle("Installation")
        self._install_dialog.setMinimumWidth(520)
        dialog_layout = QVBoxLayout(self._install_dialog)
        dialog_layout.addWidget(device_form)

        dialog_buttons = QHBoxLayout()
        self._btn_save_device = QPushButton("Enregistrer")
        self._btn_save_device.clicked.connect(self._save_current_device)
        dialog_buttons.addWidget(self._btn_save_device)

        self._btn_restore_device = QPushButton("Restaurer")
        self._btn_restore_device.setToolTip("Restaurer les valeurs enregistrées")
        self._btn_restore_device.clicked.connect(self._restore_saved_values)
        self._btn_restore_device.setEnabled(False)  # Disabled until values are modified
        dialog_buttons.addWidget(self._btn_restore_device)

        self._btn_delete_device = QPushButton("Supprimer")
        self._btn_delete_device.clicked.connect(self._delete_current_device)
        dialog_buttons.addWidget(self._btn_delete_device)

        dialog_buttons.addStretch(1)
        btn_close = QPushButton("Fermer")
        btn_close.clicked.connect(self._install_dialog.accept)
        dialog_buttons.addWidget(btn_close)
        dialog_layout.addLayout(dialog_buttons)

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
        self.btn_export = QPushButton("Enregistrer les résultats et exporter le PDF")
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

    def _show_shortcuts(self):
        """Show keyboard shortcuts dialog."""
        shortcuts = """
<h3>Navigation des coupes</h3>
<table>
<tr><td width="120"><b>←</b> / <b>→</b></td><td>Coupe précédente / suivante</td></tr>
<tr><td><b>Page↑</b> / <b>Page↓</b></td><td>Sauter 10 coupes</td></tr>
<tr><td><b>Home</b> / <b>End</b></td><td>Première / dernière coupe</td></tr>
<tr><td><b>H</b></td><td>Aller à la coupe UH</td></tr>
<tr><td><b>N</b></td><td>Aller au centre de la plage SPB</td></tr>
</table>

<h3>Affichage</h3>
<table>
<tr><td width="120"><b>F</b></td><td>Ajuster l'image à la vue</td></tr>
<tr><td><b>R</b></td><td>Réinitialiser la vue (zoom 100%)</td></tr>
<tr><td><b>U</b></td><td>Afficher/Masquer ROIs UH (jaune)</td></tr>
<tr><td><b>S</b></td><td>Afficher/Masquer ROIs SPB (vert)</td></tr>
</table>

<h3>Analyse</h3>
<table>
<tr><td width="120"><b>A</b></td><td>Inspection des artéfacts</td></tr>
</table>

<h3>Fichiers et fenêtres</h3>
<table>
<tr><td width="120"><b>Ctrl+O</b></td><td>Ouvrir un dossier DICOM</td></tr>
<tr><td><b>Ctrl+E</b></td><td>Enregistrer les résultats et exporter le PDF</td></tr>
<tr><td><b>Ctrl+I</b></td><td>Informations image DICOM</td></tr>
<tr><td><b>F1</b></td><td>Aide</td></tr>
<tr><td><b>Ctrl+Q</b></td><td>Quitter l'application</td></tr>
</table>

<h3>Contrôles souris</h3>
<table>
<tr><td width="120"><b>Molette</b></td><td>Zoom avant/arrière</td></tr>
<tr><td><b>Clic gauche</b></td><td>Déplacer l'image (pan)</td></tr>
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
<h2>CQ TDM - Aide</h2>

<h3>Introduction</h3>
<p>CQ TDM est un logiciel dédié au contrôle interne périodique des tomodensitomètres, suivant la
décision ANSM du 18/12/2025 (section 9.1.7).</p>

<h3>Utilisation</h3>
<ol>
<li><b>Ouvrir un dossier DICOM</b> (Ctrl+O) contenant les images axiales du fantôme eau</li>
<li><b>Renseigner l'installation</b> : nom, localisation, valeurs de référence pour les tests de stabilité</li>
<li><b>Ajuster les coupes d'analyse</b> :
    <ul>
    <li><i>Coupe UH</i> : coupe utilisée pour le nombre CT, l'uniformité et le bruit</li>
    <li><i>Coupes SPB</i> : plage de 10 coupes centrées pour le spectre de puissance du bruit</li>
    </ul>
</li>
<li><b>Inspecter les artéfacts</b> (touche A) : vérifiez visuellement l'absence d'artéfacts
cliniquement significatifs avec le fenêtrage ANSM (L=0, W=80)</li>
<li><b>Enregistrer les résultats et exporter le PDF</b> (Ctrl+E) : enregistre le contrôle dans l'historique de l'installation et génère le rapport conforme</li>
</ol>

<h3>Marqueurs du curseur de coupes</h3>
<ul>
<li><b>Triangle jaune</b> : position de la coupe UH (déplaçable)</li>
<li><b>Bande verte</b> : plage des coupes SPB (bords déplaçables)</li>
</ul>

<p><i>Voir menu Aide > Raccourcis clavier pour la liste complète des raccourcis.</i></p>
"""
        msg = QMessageBox(self)
        msg.setWindowTitle("Aide - CQ TDM")
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
        patient_name = img.patient_name or "N/A"
        patient_id = img.patient_id or "N/A"

        # Date and time
        datetime_str = self._format_datetime(img.study_date, img.acquisition_time or img.study_time)

        # kVp and mA with range detection for series
        kvp_str, ma_str = self._get_kvp_ma_info()

        info_lines = [
            f"Patient : {patient_name}",
            f"IPP : {patient_id}",
            f"Date : {datetime_str}",
            "",
            f"Scanner : {img.manufacturer} {img.model_name}",
            f"Station : {img.station_name or 'N/A'}",
            f"Protocole : {img.series_description or 'N/A'}",
            "",
            f"kVp : {kvp_str}",
            f"mA : {ma_str}",
            f"Filtre : {img.convolution_kernel or 'N/A'}",
            "",
            f"Matrice : {img.columns} × {img.rows} px",
            f"Taille pixel : {format_fr(img.pixel_size_mm, 3)} mm",
            f"Épaisseur coupe : {format_fr(img.slice_thickness, 1)} mm",
            f"FOV : {img.fov:.0f} mm" if img.fov else "FOV : N/A",
        ]

        if self._current_series:
            info_lines.append("")
            info_lines.append(f"Nombre de coupes : {self._current_series.num_images}")

        return "\n".join(info_lines)

    def _restore_saved_values(self):
        """Restore all saved values from the current device."""
        if self._current_device is None:
            return

        # Restore text fields
        self._edit_hospital_name.setText(self._saved_hospital_name)
        self._edit_hospital_location.setText(self._saved_hospital_location)
        self._edit_device_name.setText(self._saved_device_name)
        self._edit_commissioning_date.setText(self._saved_commissioning_date)
        self._edit_serial_number.setText(self._saved_serial_number)
        self._edit_inventory_number.setText(self._saved_inventory_number)
        self._edit_ref_noise.setText(self._saved_ref_noise)
        self._edit_ref_nps_freq.setText(self._saved_ref_nps_freq)

        self._reset_slices_to_saved()

        # Update button states
        self._update_save_button_style()

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

        # Check if any value has been modified
        self._update_save_button_style()
        self._update_install_summary()

    def _update_save_button_style(self):
        """Update the save and restore button states based on whether values are modified."""
        is_modified = self._check_any_value_modified()

        # Update restore button - enabled only when modified and device is saved
        self._btn_restore_device.setEnabled(is_modified and self._current_device is not None)

        # Update save button - enabled only when there are changes
        self._btn_save_device.setEnabled(is_modified)

        if is_modified:
            # Glowing aura effect using QGraphicsDropShadowEffect
            from PySide6.QtWidgets import QGraphicsDropShadowEffect
            from PySide6.QtGui import QColor
            glow = QGraphicsDropShadowEffect(self._btn_save_device)
            glow.setBlurRadius(15)
            glow.setOffset(0, 0)
            glow.setColor(QColor(230, 160, 48, 180))  # Amber glow
            self._btn_save_device.setGraphicsEffect(glow)
        else:
            # Remove glow effect
            self._btn_save_device.setGraphicsEffect(None)

    def _check_any_value_modified(self) -> bool:
        """Check if any saved value has been modified or if this is a new device."""
        # If no current device but image is loaded, allow saving as new device
        if self._current_device is None:
            return self._current_image is not None

        # Check text fields
        if self._edit_hospital_name.text() != self._saved_hospital_name:
            return True
        if self._edit_hospital_location.text() != self._saved_hospital_location:
            return True
        if self._edit_device_name.text() != self._saved_device_name:
            return True
        if self._edit_commissioning_date.text() != self._saved_commissioning_date:
            return True
        if self._edit_serial_number.text() != self._saved_serial_number:
            return True
        if self._edit_inventory_number.text() != self._saved_inventory_number:
            return True
        if self._edit_ref_noise.text() != self._saved_ref_noise:
            return True
        if self._edit_ref_nps_freq.text() != self._saved_ref_nps_freq:
            return True

        # Check slice values
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
        """Check if slice values have been modified and update button states."""
        # Update save and restore button states
        self._update_save_button_style()
        self._update_install_summary()
        self._update_dialog_slice_labels()
        if hasattr(self, "_btn_reset_slices"):
            _, _, differs = self._slice_selection_state()
            self._btn_reset_slices.setEnabled(differs)

    def _open_dicom_folder(self):
        """Open a folder containing DICOM files."""
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "Ouvrir dossier DICOM",
            ""
        )
        if folder_path:
            self._load_dicom_folder(folder_path)

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

    def _load_dicom_folder(self, folder_path: str):
        """Load DICOM series from folder."""
        try:
            self.statusbar.showMessage(f"Chargement du dossier: {folder_path}")
            series = load_dicom_folder(folder_path)

            if series.is_empty:
                self.statusbar.showMessage("Aucun fichier DICOM trouvé")
                message = "Aucun fichier DICOM trouvé dans ce dossier."
                if series.load_errors:
                    message += (
                        f"\n\n{len(series.load_errors)} fichier(s) n'ont pas pu être lus."
                        f"\nPremière erreur : {series.load_errors[0]}"
                    )
                QMessageBox.warning(self, "Attention", message)
                return

            self._current_series = series
            self._current_folder = str(Path(folder_path).resolve())
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
            self._update_debug_overlay()

            # Try to auto-detect device from database
            self._try_auto_detect_device()

            self.statusbar.showMessage(f"{series.num_images} images chargées depuis {Path(folder_path).name}")

            # Trigger initial analysis by emitting signals from current spinbox values
            # (set_slice_count already set default values, now trigger the analysis)
            hu_slice = self.image_viewer.get_hu_slice_index()
            nps_start, nps_end = self.image_viewer.get_nps_slice_range()
            self._on_hu_slice_changed(hu_slice)
            self._on_nps_range_changed(nps_start, nps_end)

        except Exception as e:
            self.statusbar.showMessage(f"Erreur: {e}")
            QMessageBox.critical(self, "Erreur", f"Impossible de charger le dossier DICOM:\n{e}")

    def _on_slice_changed(self, index: int):
        """Handle slice selection change."""
        if self._current_series and 0 <= index < self._current_series.num_images:
            self._current_image = self._current_series.images[index]
            self.image_viewer.set_image(self._current_image)
            # Update debug overlay if enabled
            if self._debug_mode:
                self._update_debug_overlay()

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
                kvp_str = "N/A"

            if ma_values:
                ma_min, ma_max = min(ma_values), max(ma_values)
                if ma_min == ma_max:
                    ma_str = f"{ma_min:.0f} (toutes coupes)"
                else:
                    ma_str = f"{ma_min:.0f}-{ma_max:.0f} (modulation)"
            else:
                ma_str = "N/A"
        else:
            # Single image
            img = self._current_image
            kvp_str = f"{img.kvp:.0f}" if img.kvp else "N/A"
            ma_str = f"{img.tube_current:.0f}" if img.tube_current else "N/A"

        return kvp_str, ma_str

    def _format_datetime(self, date_str: str, time_str: str) -> str:
        """Format DICOM date and time to readable format."""
        date_part = "N/A"
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

    def _format_date(self, date_str: str) -> str:
        """Format DICOM date (YYYYMMDD) to readable format."""
        if len(date_str) == 8:
            return f"{date_str[6:8]}/{date_str[4:6]}/{date_str[:4]}"
        return date_str or "N/A"

    def _export_pdf(self):
        """Export analysis results to PDF."""
        if self._current_image is None:
            QMessageBox.warning(self, "Attention", "Aucune image chargée.")
            return

        if self._current_results is None and self._nps_results is None:
            QMessageBox.warning(self, "Attention", "Aucune analyse effectuée.")
            return

        if self._current_device is None:
            answer = QMessageBox.question(
                self, "Installation non enregistrée",
                "L'installation n'est pas enregistrée : le rapport PDF sera exporté mais le contrôle "
                "ne sera pas ajouté à l'historique.\n\nEnregistrez d'abord l'installation via « Modifier… » "
                "pour conserver les résultats.\n\nExporter quand même ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return

        # Default filename with device info and the scan date (same date as the history)
        default_name = generate_report_filename(
            device_name=self._device_name,
            inventory_number=self._inventory_number,
            date=self._current_image.acquisition_date or self._current_image.study_date,
        )

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter rapport PDF",
            default_name,
            "PDF Files (*.pdf)"
        )
        if file_path:
            try:
                self.statusbar.showMessage("Génération du rapport...")

                # Create artifact result if inspection was performed
                artifact_result = None
                if self._artifact_result is not None:
                    artifact_result = ArtifactInspectionResult(
                        artifacts_present=self._artifact_result,
                        description=self._artifact_description,
                    )

                # Get reference values from text fields (works even without saved device)
                ref_noise_text = self._edit_ref_noise.text().strip()
                ref_noise = parse_float_fr(ref_noise_text) if ref_noise_text else None
                ref_nps_text = self._edit_ref_nps_freq.text().strip()
                ref_nps_freq = parse_float_fr(ref_nps_text) if ref_nps_text else None

                # Get NPS middle slice image for the report
                nps_middle_image = None
                if self._nps_results is not None and self._current_series is not None:
                    nps_start, nps_end = self.image_viewer.get_nps_slice_range()
                    # Same slice as analyze_nps uses for centre detection: slices[len // 2]
                    nps_middle_index = nps_start + (nps_end - nps_start + 1) // 2
                    if 0 <= nps_middle_index < self._current_series.num_images:
                        nps_middle_image = self._current_series.images[nps_middle_index]

                # Report on the slice that was analysed (HU slice), not the one
                # currently browsed in the viewer
                report_image = self._current_image
                if self._current_series is not None:
                    hu_index = self.image_viewer.get_hu_slice_index()
                    if 0 <= hu_index < self._current_series.num_images:
                        report_image = self._current_series.images[hu_index]

                # Previous controls only: an earlier export of this same series is
                # replaced by this one in the history, so it must not appear as a
                # separate past control in the report
                history = []
                if self._current_device is not None:
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
                )
                history_msg = self._record_run_in_history(file_path)
                self.statusbar.showMessage(f"Rapport exporté: {file_path}{history_msg}")

                # Open PDF directly with default viewer
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))
            except Exception as e:
                self.statusbar.showMessage(f"Erreur d'export: {e}")
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'export:\n{e}")

    def _export_nps_rois_json(self):
        """Export NPS ROI positions to JSON file."""
        import json

        if self._nps_results is None:
            QMessageBox.warning(self, "Attention", "Aucune analyse SPB effectuée.")
            return

        # Default filename
        from datetime import datetime
        default_name = f"NPS_ROIs_Position_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter positions ROIs SPB",
            default_name,
            "JSON Files (*.json)"
        )
        if file_path:
            try:
                roi_data = self._nps_results.roi_config.to_dict()
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(roi_data, f, indent='\t', ensure_ascii=False)
                self.statusbar.showMessage(f"ROIs SPB exportées: {file_path}")
            except Exception as e:
                self.statusbar.showMessage(f"Erreur d'export: {e}")
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'export:\n{e}")

    def _export_hu_rois_json(self):
        """Export HU ROI positions to JSON file."""
        import json

        if self._current_results is None:
            QMessageBox.warning(self, "Attention", "Aucune analyse UH effectuée.")
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
            "JSON Files (*.json)"
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(roi_data, f, indent='\t', ensure_ascii=False)
                self.statusbar.showMessage(f"ROIs UH exportées: {file_path}")
            except Exception as e:
                self.statusbar.showMessage(f"Erreur d'export: {e}")
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'export:\n{e}")

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

            # Run HU analysis
            rois = calculate_rois(hu_image)
            results = analyze_water_phantom(hu_image, rois)
            self._current_results = results

            # Display ROIs
            self._display_rois(rois, slice_index)

            # Update results display
            self._update_results_display()
            self.btn_export.setEnabled(True)

        except Exception as e:
            self._current_results = None
            # No measurement: do not keep drawing ROIs that no longer mean anything
            self.image_viewer.viewer.clear_water_rois()
            self._update_results_display()
            self.btn_export.setEnabled(self._nps_results is not None)
            self.statusbar.showMessage(f"Erreur analyse UH: {e}")

    def _on_nps_range_changed(self, start: int, end: int):
        """Handle NPS range change - debounce before re-running analysis."""
        if self._current_series is None:
            return

        # Store pending range and restart debounce timer
        self._pending_nps_range = (start, end)
        self._nps_debounce_timer.start()
        self.statusbar.showMessage("Modification de la plage SPB...")

        # Check if values differ from saved
        self._check_slice_values_modified()

    def _run_debounced_nps_analysis(self):
        """Run NPS analysis after debounce delay."""
        if self._current_series is None or self._pending_nps_range is None:
            return

        start, end = self._pending_nps_range
        self._pending_nps_range = None

        try:
            self.statusbar.showMessage("Analyse SPB en cours...")
            # Run NPS analysis with new range
            nps_result = analyze_nps(self._current_series, slice_range=(start, end))
            self._nps_results = nps_result

            # Display NPS ROI
            self._display_nps_roi(nps_result, start, end)

            # Update results display
            self._update_results_display()
            self.btn_export.setEnabled(True)
            self.statusbar.showMessage("Analyse SPB terminée", 3000)

        except Exception as e:
            self._nps_results = None
            self.image_viewer.viewer.clear_nps_rois()
            self._update_results_display()
            self.btn_export.setEnabled(self._current_results is not None)
            self.statusbar.showMessage(f"Erreur analyse SPB: {e}")

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
            # Detect phantom center and diameter
            center = detect_phantom_center(self._current_image)
            diameter = estimate_phantom_diameter(self._current_image, center)
            radius = diameter / 2
            viewer.set_debug_phantom(center, radius)
        else:
            viewer.set_debug_phantom(None, None)

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
            QMessageBox.warning(self, "Attention", "Aucune image chargée.")
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

    def _warn_device_db_load_error(self):
        """Tell the user the device database file could not be read."""
        QMessageBox.warning(
            self,
            "Base de données des appareils",
            "Le fichier de la base de données des appareils n'a pas pu être lu.\n"
            "Une copie a été enregistrée avec l'extension .bak et l'application "
            "démarre avec une base vide.\n\n"
            f"Détail : {self._device_db.load_error}",
        )

    def _update_results_display(self):
        """Update the results browser with current results."""
        html = self._format_results_html()
        self.results_browser.setHtml(html)
        self.history_panel.set_current_run(self._current_run_for_history())

    # ---- results history ----

    def _refresh_history(self, device: DeviceConfig | None):
        """Fill the history tab for the selected device."""
        if device is None:
            self.history_panel.set_runs([], None, None)
            return
        self.history_panel.set_runs(list(device.runs), device.reference_noise, device.reference_nps_freq)

    def _current_run_for_history(self) -> QCRun | None:
        """Build a QCRun from the measurement on screen, or None if nothing is analysed yet."""
        if self._current_results is None:
            return None
        from datetime import datetime
        from cq_tdm import __version__

        img = self._current_image
        if self._current_series is not None:
            hu_index = self.image_viewer.get_hu_slice_index()
            if 0 <= hu_index < self._current_series.num_images:
                img = self._current_series.images[hu_index]
        nps_start, nps_end = self.image_viewer.get_nps_slice_range()
        return QCRun(
            run_date=dicom_date_to_iso(img.acquisition_date if img is not None else ""),
            series_uid=img.series_instance_uid if img is not None else "",
            kvp=float(img.kvp) if img is not None and img.kvp else 0.0,
            mas=float(img.mas) if img is not None else 0.0,
            slice_thickness=float(img.slice_thickness) if img is not None else 0.0,
            kernel=img.convolution_kernel if img is not None else "",
            water_ct=self._current_results.water_ct_number,
            uniformity=self._current_results.uniformity,
            noise=self._current_results.noise,
            nps_freq=self._nps_results.mean_frequency if self._nps_results is not None else None,
            artifacts_present=self._artifact_result,
            artifacts_description=self._artifact_description if self._artifact_result else "",
            hu_slice_index=self.image_viewer.get_hu_slice_index(),
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
        ref_noise_text = self._edit_ref_noise.text().strip()
        ref_nps_text = self._edit_ref_nps_freq.text().strip()
        run.ref_noise = parse_float_fr(ref_noise_text) if ref_noise_text else None
        run.ref_nps_freq = parse_float_fr(ref_nps_text) if ref_nps_text else None
        try:
            replaced = self._device_db.add_run(self._current_device.device_id, run)
        except OSError as e:
            QMessageBox.warning(
                self, "Historique",
                f"Le rapport a été exporté mais le contrôle n'a pas pu être enregistré dans l'historique :\n{e}")
            return ""
        self._refresh_history(self._current_device)
        self.history_panel.select_run(self._current_device.find_run(run.run_id))
        return " · contrôle remplacé dans l'historique" if replaced else " · contrôle ajouté à l'historique"

    def _apply_reference_from_history(self, run: QCRun):
        """Make a past run's noise and SPB frequency the device reference values."""
        nps_text = f" et f SPB = {format_fr(run.nps_freq, 3)} c/mm" if run.nps_freq is not None else ""
        answer = QMessageBox.question(
            self, "Valeurs de référence",
            f"Définir σ = {format_fr(run.noise, 2)} HU{nps_text} (contrôle du {run.date_fr()}) "
            "comme valeurs de référence de cette installation ?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._edit_ref_noise.setText(format_fr(run.noise, 2))
        if run.nps_freq is not None:
            self._edit_ref_nps_freq.setText(format_fr(run.nps_freq, 3))
        if self._current_device is not None:
            self._current_device.reference_noise = run.noise
            self._current_device.reference_nps_freq = run.nps_freq
            try:
                self._device_db.save_device(self._current_device)
            except OSError as e:
                QMessageBox.warning(self, "Historique", f"Impossible d'enregistrer les valeurs de référence :\n{e}")
                return
            self._saved_ref_noise = self._edit_ref_noise.text()
            self._saved_ref_nps_freq = self._edit_ref_nps_freq.text()
            self._update_save_button_style()
            self._update_install_summary()
        self.statusbar.showMessage(f"Valeurs de référence définies depuis le contrôle du {run.date_fr()}", 5000)

    def _delete_history_run(self, run: QCRun):
        if self._current_device is None:
            return
        try:
            self._device_db.delete_run(self._current_device.device_id, run.run_id)
        except OSError as e:
            QMessageBox.warning(self, "Historique", f"Impossible de supprimer le contrôle :\n{e}")
            return
        self._refresh_history(self._current_device)

    def _relink_history_pdf(self, run: QCRun, new_path: str):
        if self._current_device is None:
            return
        run.pdf_path = new_path
        try:
            self._device_db.update_run(self._current_device.device_id, run)
        except (OSError, ValueError) as e:
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
        if self._current_device is None:
            return
        folder = resolve_dicom_folder(
            run.series_uid, run.dicom_folder, run.dicom_folder_rel,
            self._device_db.db_path, self._device_db.folder_relocations)
        if folder is None:
            folder = self._ask_run_folder(run)
            if folder is None:
                return
        self._load_dicom_folder(str(folder))
        if self._current_series is None or self._current_image is None \
                or self._current_image.series_instance_uid != run.series_uid:
            QMessageBox.warning(
                self, "Série différente",
                f"Les images chargées depuis {folder} n'appartiennent pas à la série du contrôle "
                f"du {run.date_fr()}.")
            return
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
        box.addButton(QMessageBox.StandardButton.Cancel)
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
        from PySide6.QtWidgets import QApplication, QProgressDialog
        progress = QProgressDialog("Recherche de la série…", "Annuler", 0, 0, self)
        progress.setWindowTitle("Recherche")
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
        except (OSError, ValueError) as e:
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

        html_parts = [css]

        # Water phantom results
        if self._current_results:
            html_parts.append(self._format_water_results_html())

        # NPS results
        if self._nps_results:
            html_parts.append(self._format_nps_html())

        # Artifact inspection results
        html_parts.append(self._format_artifact_html())

        return "".join(html_parts)

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

        return f"""
        <div class="section">
            <div class="section-title">Nombre CT de l'eau</div>
            <table>
                <tr><th>Valeur centrale</th><td class="value">{format_fr(r.water_ct_number, 1, sign=True)} HU</td></tr>
                <tr><th>Critère</th><td>±7 HU (NCG: ±25 HU)</td></tr>
                <tr><th>Statut</th><td>{status_html(r.water_ct_acceptable, r.water_ct_ncg)}</td></tr>
            </table>
        </div>

        <div class="section">
            <div class="section-title">Uniformité</div>
            <table>
                <tr><th>Écart max C-P</th><td class="value">{format_fr(r.uniformity, 1)} HU</td></tr>
                <tr><th>Critère</th><td>≤7 HU</td></tr>
                <tr><th>Statut</th><td>{status_html(r.uniformity_acceptable)}</td></tr>
            </table>
        </div>

        <div class="section">
            <div class="section-title">Bruit</div>
            <table>
                <tr><th>Écart-type central</th><td class="value">{format_fr(r.noise, 2)} HU</td></tr>
                {self._format_noise_stability_html(r.noise)}
            </table>
        </div>

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
                <tr><th>Nombre de ROIs</th><td class="value">{num_rois}</td></tr>
                <tr><th>Taille ROI</th><td class="value">{r.roi_size} × {r.roi_size} px</td></tr>
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
        ax.set_ylabel('SPB (HU²·mm²)', color='#aaa', fontsize=9)
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
        plt.close(fig)
        buffer.seek(0)

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
                import html
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
                <tr><th>Fenêtre utilisée</th><td class="value">W: 80, L: 0 UH</td></tr>
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
                <tr><th>Valeur de référence</th><td class="value">{format_fr(ref_noise, 2)} HU</td></tr>
                <tr><th>Écart</th><td class="value">{format_fr(deviation, 2, sign=True)} HU</td></tr>
                <tr><th>Critère</th><td>[{format_fr(lower_bound, 2, sign=True)}, {format_fr(upper_bound, 2, sign=True)}] HU</td></tr>
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
        self._device_combo.addItem("-- Nouvel équipement --", None)

        devices = self._device_db.get_all_devices()
        selected_index = 0
        for i, device in enumerate(devices):
            self._device_combo.addItem(device.display_name(), device.device_id)
            if current_id and device.device_id == current_id:
                selected_index = i + 1  # +1 for the "Nouvel équipement" item

        self._device_combo.setCurrentIndex(selected_index)
        self._device_combo.blockSignals(False)

    def _on_device_selected(self, index: int):
        """Handle device selection from dropdown."""
        device_id = self._device_combo.itemData(index)
        if device_id is None:
            # "Nouvel équipement" selected - reset to defaults
            self._current_device = None
            self._load_device_config(None)
        else:
            device = self._device_db.get_device(device_id)
            if device:
                self._current_device = device
                self._load_device_config(device)

    def _edit_installation(self):
        """Open the installation form."""
        self._update_dialog_slice_labels()
        self._install_dialog.exec()
        self._update_install_summary()

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

    def _update_dialog_slice_labels(self):
        """Show the current and saved slice selection inside the Installation dialog."""
        if not hasattr(self, "_label_dialog_hu_slice"):
            return
        c = _theme_colors()
        if self._current_series is None:
            self._label_dialog_hu_slice.setText("—")
            self._label_dialog_nps_range.setText("—")
            if self._saved_hu_slice is not None:
                self._label_dialog_hu_slice.setText(f"{self._saved_hu_slice + 1} (enregistrée)")
            if self._saved_nps_start is not None and self._saved_nps_end is not None:
                self._label_dialog_nps_range.setText(
                    f"{self._saved_nps_start + 1} – {self._saved_nps_end + 1} (enregistrées)")
            self._label_dialog_slices_note.setText("Aucune série chargée : les coupes enregistrées sont conservées.")
            self._label_dialog_slices_note.setStyleSheet("font-size: 11px; color: #888;")
            return

        hu = self.image_viewer.get_hu_slice_index()
        start, end = self.image_viewer.get_nps_slice_range()
        hu_text = str(hu + 1)
        nps_text = f"{start + 1} – {end + 1}"
        _, saved, differs = self._slice_selection_state()
        if differs:
            if self._saved_hu_slice is not None and hu != self._saved_hu_slice:
                hu_text += f"   (enregistrée : {self._saved_hu_slice + 1})"
            if (self._saved_nps_start, self._saved_nps_end) != (start, end):
                nps_text += f"   (enregistrées : {self._saved_nps_start + 1} – {self._saved_nps_end + 1})"
            self._label_dialog_slices_note.setText(
                "La sélection actuelle diffère des coupes enregistrées. "
                "« Enregistrer » remplacera les coupes enregistrées par la sélection actuelle.")
            self._label_dialog_slices_note.setStyleSheet(f"font-size: 11px; color: {c['warning_border']};")
        elif saved is None:
            self._label_dialog_slices_note.setText(
                "Coupes non encore enregistrées pour cette installation : « Enregistrer » les mémorisera.")
            self._label_dialog_slices_note.setStyleSheet("font-size: 11px; color: #888;")
        else:
            self._label_dialog_slices_note.setText("Sélection identique aux coupes enregistrées.")
            self._label_dialog_slices_note.setStyleSheet("font-size: 11px; color: #888;")
        self._label_dialog_hu_slice.setText(hu_text)
        self._label_dialog_nps_range.setText(nps_text)

    def _update_install_summary(self):
        """Refresh the one-glance summary shown under the device selector."""
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
            saved = " · ".join(parts) if parts else "Aucune information renseignée"

        noise_text = self._edit_ref_noise.text().strip()
        nps_text = self._edit_ref_nps_freq.text().strip()
        ref_noise = parse_float_fr(noise_text) if noise_text else None
        ref_nps = parse_float_fr(nps_text) if nps_text else None
        refs = []
        if ref_noise is not None:
            refs.append(f"σ réf. {format_fr(ref_noise, 2)} HU")
        if ref_nps is not None:
            refs.append(f"f SPB réf. {format_fr(ref_nps, 3)} c/mm")
        ref_line = " · ".join(refs) if refs else (
            f'<span style="color:{c["warning_border"]};">Valeurs de référence non renseignées</span>'
        )
        modified = " · <i>modifications non enregistrées</i>" if self._check_any_value_modified() else ""
        current, saved_slices, differs = self._slice_selection_state()
        if differs:
            slice_line = (f'<br><span style="color:{c["warning_border"]};">Coupes {current} ≠ enregistrées '
                          f'({saved_slices}) — cliquer sur « Modifier… » pour les enregistrer</span>')
        elif current and saved_slices is None and self._current_device is not None:
            slice_line = f"<br>Coupes {current} · <i>non enregistrées</i>"
        elif current:
            slice_line = f"<br>Coupes {current}"
        else:
            slice_line = ""
        self._install_summary.setText(f"{saved}<br>{ref_line}{modified}{slice_line}")

    def _load_device_config(self, device: DeviceConfig | None):
        """Load device configuration into the UI fields."""
        self._refresh_history(device)
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
            # Clear saved field values
            self._saved_hospital_name = ""
            self._saved_hospital_location = ""
            self._saved_device_name = ""
            self._saved_commissioning_date = ""
            self._saved_serial_number = ""
            self._saved_inventory_number = ""
            self._saved_ref_noise = ""
            self._saved_ref_nps_freq = ""
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
            # Apply slice values if they exist and we have a series loaded
            if self._current_series is not None:
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
                if device.hu_slice_index is not None:
                    self.image_viewer.set_hu_slice_index(device.hu_slice_index)
                if device.nps_start_slice is not None and device.nps_end_slice is not None:
                    self.image_viewer.set_nps_slice_range(device.nps_start_slice, device.nps_end_slice)

            # Store saved field values for modification tracking
            self._saved_hospital_name = self._edit_hospital_name.text()
            self._saved_hospital_location = self._edit_hospital_location.text()
            self._saved_device_name = self._edit_device_name.text()
            self._saved_commissioning_date = self._edit_commissioning_date.text()
            self._saved_serial_number = self._edit_serial_number.text()
            self._saved_inventory_number = self._edit_inventory_number.text()
            self._saved_ref_noise = self._edit_ref_noise.text()
            self._saved_ref_nps_freq = self._edit_ref_nps_freq.text()

        # Update button states
        self._update_save_button_style()

    def _save_current_device(self):
        """Save the current device configuration to the database."""
        if self._current_image is None:
            QMessageBox.warning(
                self,
                "Attention",
                "Veuillez d'abord charger une image DICOM pour identifier l'installation."
            )
            return

        # Check if database file exists, prompt to create if not
        if not self._device_db.db_path.exists():
            reply = QMessageBox.question(
                self,
                "Base de données introuvable",
                f"Le fichier de base de données n'existe pas :\n{self._device_db.db_path}\n\n"
                "Voulez-vous le créer maintenant ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply == QMessageBox.StandardButton.Yes:
                # Create the database file
                self._device_db.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._device_db._save()  # This will create the file
            else:
                return

        # Create or update device config
        img = self._current_image
        if self._current_device is None:
            # The id is derived from the DICOM identity, so a device saved earlier
            # for this scanner must be updated, not replaced (that would wipe its
            # recorded controls)
            device = self._device_db.find_device(
                img.manufacturer or "",
                img.model_name or "",
                img.station_name or "",
                img.device_serial_number or "",
            )
            if device is None:
                # Create new device from DICOM metadata
                device = DeviceConfig.from_dicom(
                    img.manufacturer or "",
                    img.model_name or "",
                    img.station_name or "",
                    img.device_serial_number or "",
                )
        else:
            device = self._current_device

        # Update with current field values
        device.hospital_name = self._hospital_name
        device.hospital_location = self._hospital_location
        device.device_name = self._device_name
        device.commissioning_date = self._commissioning_date
        device.serial_number = self._serial_number
        device.inventory_number = self._inventory_number

        # Update reference values (accept both dot and comma as decimal separator)
        ref_noise_text = self._edit_ref_noise.text().strip()
        device.reference_noise = parse_float_fr(ref_noise_text) if ref_noise_text else None

        ref_freq_text = self._edit_ref_nps_freq.text().strip()
        device.reference_nps_freq = parse_float_fr(ref_freq_text) if ref_freq_text else None

        # Save slice values
        device.hu_slice_index = self.image_viewer.get_hu_slice_index()
        nps_start, nps_end = self.image_viewer.get_nps_slice_range()
        device.nps_start_slice = nps_start
        device.nps_end_slice = nps_end

        # Save to database
        self._device_db.save_device(device)
        self._current_device = device

        # Always save database path in settings
        config = get_app_config()
        if config.device_database_path != str(self._device_db.db_path):
            config.device_database_path = str(self._device_db.db_path)
            save_app_config()

        # Update saved slice values
        self._saved_hu_slice = device.hu_slice_index
        self._saved_nps_start = device.nps_start_slice
        self._saved_nps_end = device.nps_end_slice

        # Update saved field values
        self._saved_hospital_name = self._edit_hospital_name.text()
        self._saved_hospital_location = self._edit_hospital_location.text()
        self._saved_device_name = self._edit_device_name.text()
        self._saved_commissioning_date = self._edit_commissioning_date.text()
        self._saved_serial_number = self._edit_serial_number.text()
        self._saved_inventory_number = self._edit_inventory_number.text()
        self._saved_ref_noise = self._edit_ref_noise.text()
        self._saved_ref_nps_freq = self._edit_ref_nps_freq.text()

        # Reset button states
        self._update_save_button_style()

        # Refresh combo and select the saved device
        self._refresh_device_combo()

        # History tab now evaluates against the saved references
        self._refresh_history(device)

        # Re-run analysis display to compare with new reference values
        self._update_results_display()

        self.statusbar.showMessage(f"Installation '{device.display_name()}' enregistrée")
        self._update_install_summary()
        self._update_dialog_slice_labels()

    def _delete_current_device(self):
        """Delete the currently selected device from the database."""
        if self._current_device is None:
            QMessageBox.warning(
                self,
                "Attention",
                "Aucune installation sélectionnée à supprimer."
            )
            return

        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Confirmer la suppression",
            f"Voulez-vous vraiment supprimer l'installation '{self._current_device.display_name()}' ?"
            + (f"\n\nSes {len(self._current_device.runs)} contrôles enregistrés seront perdus."
               if self._current_device.runs else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            device_name = self._current_device.display_name()
            self._device_db.delete_device(self._current_device.device_id)
            self._current_device = None
            self._refresh_device_combo()
            self._load_device_config(None)
            self.statusbar.showMessage(f"Installation '{device_name}' supprimée")

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
            self.statusbar.showMessage(
                f"Installation reconnue : {device.display_name()}"
            )
        else:
            # Device not found: clear everything that belonged to the previous
            # device (references, identity, history) so the new scanner is not
            # judged against, or reported with, another installation's values
            self._current_device = None
            self._load_device_config(None)
            # Pre-fill device name from DICOM
            device_name = f"{img.manufacturer or ''} {img.model_name or ''}".strip()
            if device_name:
                self._edit_device_name.setText(device_name)
            # Set combo to "Nouvel équipement"
            self._device_combo.blockSignals(True)
            self._device_combo.setCurrentIndex(0)
            self._device_combo.blockSignals(False)
            self._update_install_summary()

    def _show_device_manager(self):
        """Show the device management dialog."""
        dialog = DeviceManagerDialog(self._device_db, self)
        dialog.exec()
        # Update device_db reference if it was changed in the dialog
        self._device_db = dialog.device_db
        # The dialog may have edited, deleted or replaced (new database) the
        # device shown here: resolve it again by id and reload its values so the
        # results panel, the PDF and the history never use a stale object
        current_id = self._current_device.device_id if self._current_device else None
        self._current_device = self._device_db.get_device(current_id) if current_id else None
        self._load_device_config(self._current_device)
        # Refresh combo after dialog closes (in case devices were modified)
        self._refresh_device_combo()
        self._update_results_display()
        self._update_install_summary()
