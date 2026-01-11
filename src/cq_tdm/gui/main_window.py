"""Main application window."""

import base64
from io import BytesIO
from pathlib import Path

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt

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
)

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
    DeviceConfig,
    DeviceDatabase,
    get_app_config,
    save_app_config,
    format_fr,
    parse_float_fr,
)
from ..reports import generate_pdf_report, ArtifactInspectionResult
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
            Path(file_path).write_text('{"version": 1, "devices": []}', encoding="utf-8")
            self._switch_database(file_path)

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


class MainWindow(QMainWindow):
    """Main application window for CQ TDM."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CQ TDM - Contrôle Qualité Tomodensitométrie")
        self.setMinimumSize(1200, 800)

        self._current_image: DicomImage | None = None
        self._current_series: DicomSeries | None = None
        self._current_results: WaterPhantomResults | None = None
        self._nps_results: NPSResult | None = None
        self._artifact_result: bool | None = None  # True=present (NC), False=absent (Conforme)
        self._artifact_description: str = ""  # Description of artifacts if present
        self._debug_mode: bool = False  # Debug mode for phantom detection visualization

        # Device database and current device (use custom path from config if set)
        config = get_app_config()
        db_path = Path(config.device_database_path) if config.device_database_path else None
        self._device_db = DeviceDatabase(db_path)
        self._current_device: DeviceConfig | None = None

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

        self._btn_save_device = QPushButton("Enregistrer")
        self._btn_save_device.setMaximumWidth(100)
        self._btn_save_device.clicked.connect(self._save_current_device)
        device_selector_layout.addWidget(self._btn_save_device)

        self._btn_restore_device = QPushButton("Restaurer")
        self._btn_restore_device.setMaximumWidth(80)
        self._btn_restore_device.setToolTip("Restaurer les valeurs enregistrées")
        self._btn_restore_device.clicked.connect(self._restore_saved_values)
        self._btn_restore_device.setEnabled(False)  # Disabled until values are modified
        device_selector_layout.addWidget(self._btn_restore_device)

        self._btn_delete_device = QPushButton("Supprimer")
        self._btn_delete_device.setMaximumWidth(80)
        self._btn_delete_device.clicked.connect(self._delete_current_device)
        device_selector_layout.addWidget(self._btn_delete_device)

        right_layout.addLayout(device_selector_layout)

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
        self._edit_ref_noise = QLineEdit()
        self._edit_ref_noise.setPlaceholderText("Bruit de référence (HU)")
        self._edit_ref_nps_freq = QLineEdit()
        self._edit_ref_nps_freq.setPlaceholderText("Fréq. moyenne SPB réf. (cycles/mm)")

        # Connect signals to update instance variables and check for modifications
        self._edit_hospital_name.textChanged.connect(self._on_field_changed)
        self._edit_hospital_location.textChanged.connect(self._on_field_changed)
        self._edit_device_name.textChanged.connect(self._on_field_changed)
        self._edit_commissioning_date.textChanged.connect(self._on_field_changed)
        self._edit_serial_number.textChanged.connect(self._on_field_changed)
        self._edit_inventory_number.textChanged.connect(self._on_field_changed)
        self._edit_ref_noise.textChanged.connect(self._on_field_changed)
        self._edit_ref_nps_freq.textChanged.connect(self._on_field_changed)

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

        right_layout.addWidget(device_form)
        right_layout.addSpacing(15)

        # Slice selection controls (from image viewer)
        slice_controls_label = QLabel("Sélection des coupes")
        slice_controls_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(slice_controls_label)

        right_layout.addWidget(self.image_viewer.slice_controls_widget)

        right_layout.addSpacing(15)

        # Results section
        results_label = QLabel("Résultats d'analyse")
        results_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(results_label)

        self.results_browser = QTextBrowser()
        self.results_browser.setOpenExternalLinks(False)
        self.results_browser.setStyleSheet("""
            QTextBrowser {
                background-color: #2b2b2b;
                color: #e0e0e0;
                border: 1px solid #444;
                border-radius: 4px;
                font-size: 12px;
            }
        """)
        self.results_browser.setHtml(self._get_empty_results_html())
        right_layout.addWidget(self.results_browser, 1)

        # Artifact inspection button
        self.btn_artifact = QPushButton("Inspection artéfacts")
        self.btn_artifact.setEnabled(False)
        self.btn_artifact.clicked.connect(self._inspect_artifacts)
        right_layout.addWidget(self.btn_artifact)

        # Export button
        self.btn_export = QPushButton("Exporter PDF")
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
        from PySide6.QtWidgets import QMessageBox
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
<tr><td><b>Ctrl+E</b></td><td>Exporter le rapport PDF</td></tr>
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
        from PySide6.QtWidgets import QMessageBox
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
<li><b>Exporter le rapport PDF</b> (Ctrl+E) : génère un rapport conforme incluant tous les résultats</li>
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

        # Restore slice values
        if self._saved_hu_slice is not None:
            self.image_viewer.set_hu_slice_index(self._saved_hu_slice)
        if self._saved_nps_start is not None and self._saved_nps_end is not None:
            self.image_viewer.set_nps_slice_range(self._saved_nps_start, self._saved_nps_end)

        # Update button states
        self._update_save_button_style()

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

    def _open_dicom_folder(self):
        """Open a folder containing DICOM files."""
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "Ouvrir dossier DICOM",
            ""
        )
        if folder_path:
            self._load_dicom_folder(folder_path)

    def _load_dicom_folder(self, folder_path: str):
        """Load DICOM series from folder."""
        try:
            self.statusbar.showMessage(f"Chargement du dossier: {folder_path}")
            series = load_dicom_folder(folder_path)

            if series.is_empty:
                self.statusbar.showMessage("Aucun fichier DICOM trouvé")
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Attention", "Aucun fichier DICOM trouvé dans ce dossier.")
                return

            self._current_series = series
            self._artifact_result = None  # Reset artifact result for new series
            self._artifact_description = ""

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
            from PySide6.QtWidgets import QMessageBox
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
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Attention", "Aucune image chargée.")
            return

        if self._current_results is None and self._nps_results is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Attention", "Aucune analyse effectuée.")
            return

        # Default filename with date
        from datetime import datetime
        default_name = f"rapport_cq_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter rapport PDF",
            default_name,
            "PDF Files (*.pdf)"
        )
        if file_path:
            try:
                self.statusbar.showMessage(f"Génération du rapport...")

                # Create artifact result if inspection was performed
                artifact_result = None
                if self._artifact_result is not None:
                    artifact_result = ArtifactInspectionResult(
                        artifacts_present=self._artifact_result,
                        description=self._artifact_description,
                    )

                # Get reference values from current device
                ref_noise = None
                ref_nps_freq = None
                if self._current_device is not None:
                    ref_noise = self._current_device.reference_noise
                    ref_nps_freq = self._current_device.reference_nps_freq

                # Get NPS middle slice image for the report
                nps_middle_image = None
                if self._nps_results is not None and self._current_series is not None:
                    nps_start, nps_end = self.image_viewer.get_nps_slice_range()
                    nps_middle_index = (nps_start + nps_end) // 2
                    if 0 <= nps_middle_index < self._current_series.num_images:
                        nps_middle_image = self._current_series.images[nps_middle_index]

                generate_pdf_report(
                    file_path,
                    self._current_image,
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
                )
                self.statusbar.showMessage(f"Rapport exporté: {file_path}")

                # Open PDF directly with default viewer
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))
            except Exception as e:
                self.statusbar.showMessage(f"Erreur d'export: {e}")
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'export:\n{e}")

    def _export_nps_rois_json(self):
        """Export NPS ROI positions to JSON file."""
        import json

        if self._nps_results is None:
            from PySide6.QtWidgets import QMessageBox
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
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'export:\n{e}")

    def _export_hu_rois_json(self):
        """Export HU ROI positions to JSON file."""
        import json

        if self._current_results is None:
            from PySide6.QtWidgets import QMessageBox
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
                from PySide6.QtWidgets import QMessageBox
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

    def _run_nps_analysis(self):
        """Run NPS analysis using spinbox slice range."""
        if self._current_series is None:
            return

        try:
            # Get slice range from spinboxes
            nps_start, nps_end = self.image_viewer.get_nps_slice_range()

            nps_result = analyze_nps(self._current_series, slice_range=(nps_start, nps_end))
            self._nps_results = nps_result

            # Display NPS ROI (square)
            self._display_nps_roi(nps_result, nps_start, nps_end)
            self.btn_export.setEnabled(True)

        except Exception as e:
            self.statusbar.showMessage(f"Erreur SPB: {e}")
            self._nps_results = None

    def _display_nps_roi(self, nps_result: NPSResult, start_slice: int, end_slice: int):
        """Display all 8 NPS ROIs on the image viewer."""
        from PySide6.QtGui import QColor

        if self._current_series is None:
            return

        # Get ROI positions from the analysis result
        roi_positions = nps_result.roi_config.rois
        half_size = nps_result.roi_size // 2

        # Create ROI objects for display
        roi_list = []
        roi_colors = [
            QColor(0, 255, 0),    # Green for all ROIs
        ]

        for i, roi_pos in enumerate(roi_positions):
            roi = ROI(
                center_x=roi_pos.x,
                center_y=roi_pos.y,
                radius=half_size,
                name=f"SPB{i+1}",
                color=QColor(0, 255, 0),  # Green
                is_square=True,
            )
            roi_list.append(roi)

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
            self.statusbar.showMessage(f"Erreur analyse SPB: {e}")

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
        from PySide6.QtWidgets import QMessageBox
        about_text = """
<h2>CQ TDM</h2>
<p><b>Version 0.1.0</b></p>

<p>Logiciel pour le contrôle de qualité interne des tomodensitomètres.</p>

<h3>Auteur</h3>
<p>Luis Ammour — <a href="mailto:luis@ammour.net">luis@ammour.net</a></p>

<h3>Cadre réglementaire</h3>
<p>Le logiciel tente d'être conforme à la décision ANSM du 18/12/2025 fixant les modalités
du contrôle de qualité des tomodensitomètres. L'auteur ne garantit pas les résultats.</p>

<p><i>Utilisez ce logiciel à vos risques et périls !</i></p>

<h3>Licence</h3>
<p>Tous droits réservés.</p>
"""
        msg = QMessageBox(self)
        msg.setWindowTitle("À propos de CQ TDM")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(about_text)
        msg.exec()

    def _inspect_artifacts(self):
        """Open the artifact inspection dialog."""
        if self._current_image is None:
            from PySide6.QtWidgets import QMessageBox
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

    def _get_empty_results_html(self) -> str:
        """Return HTML for empty results state."""
        return """
        <div style="color: #888; text-align: center; padding: 40px;">
            <p>Aucune analyse effectuée</p>
            <p style="font-size: 11px;">Ouvrez un dossier DICOM pour lancer l'analyse</p>
        </div>
        """

    def _update_results_display(self):
        """Update the results browser with current results."""
        html = self._format_results_html()
        self.results_browser.setHtml(html)

    def _format_results_html(self) -> str:
        """Format all results as HTML."""
        if self._current_results is None and self._nps_results is None and self._artifact_result is None:
            return self._get_empty_results_html()

        css = """
        <style>
            body { font-family: -apple-system, sans-serif; font-size: 12px; color: #e0e0e0; margin: 8px; }
            .section { margin-bottom: 16px; }
            .section-title {
                font-weight: bold;
                font-size: 13px;
                color: #4fc3f7;
                border-bottom: 1px solid #4fc3f7;
                padding-bottom: 4px;
                margin-bottom: 8px;
            }
            table { width: 100%; border-collapse: collapse; margin: 4px 0; }
            th, td { padding: 4px 8px; text-align: left; }
            th { color: #aaa; font-weight: normal; width: 45%; }
            td { color: #fff; }
            .ok { color: #4caf50; font-weight: bold; }
            .nc { color: #ff9800; font-weight: bold; }
            .ncg { color: #f44336; font-weight: bold; }
            .pending { color: #888; font-style: italic; }
            .value { font-family: monospace; }
            .roi-table th { width: 20%; text-align: center; }
            .roi-table td { text-align: center; font-family: monospace; }
            .roi-header { background-color: #333; }
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
                <tr><th>Critère</th><td>≤7 HU (NCG: &gt;25 HU)</td></tr>
                <tr><th>Statut</th><td>{status_html(r.uniformity_acceptable, r.uniformity_ncg)}</td></tr>
            </table>
        </div>

        <div class="section">
            <div class="section-title">Bruit</div>
            <table>
                <tr><th>Écart-type central</th><td class="value">{format_fr(r.noise, 1)} HU</td></tr>
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

        # Warning for insufficient slices (ANSM requires 10 slices)
        if r.num_slices < 10:
            warnings_html += f"""
            <div style="margin-top: 8px; padding: 8px; background-color: #4a3000; border-radius: 4px; border-left: 3px solid #ff9800;">
                <div style="color: #ff9800; font-weight: bold; margin-bottom: 4px;">⚠ Nombre de coupes insuffisant</div>
                <div style="color: #ffcc80; font-size: 11px;">
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
            <div style="margin-top: 8px; padding: 8px; background-color: #4a3000; border-radius: 4px; border-left: 3px solid #ff9800;">
                <div style="color: #ff9800; font-weight: bold; margin-bottom: 4px;">⚠ Attention : contenu non uniforme détecté</div>
                <ul style="margin: 0; padding-left: 20px; color: #ffcc80; font-size: 11px;">
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
        if self._current_device is None or self._current_device.reference_noise is None:
            return ""

        ref_noise = self._current_device.reference_noise
        deviation = noise - ref_noise

        # ANSM criterion: MIN(-0.2, -0.1*B_ref) ≤ (B_i - B_ref) ≤ MAX(0.2, 0.1*B_ref)
        lower_bound = min(-0.2, -0.1 * ref_noise)
        upper_bound = max(0.2, 0.1 * ref_noise)
        is_conforme = lower_bound <= deviation <= upper_bound

        if is_conforme:
            status_html = '<span class="ok">✓ Conforme</span>'
        else:
            status_html = '<span class="nc">✗ Non conforme</span>'

        return f"""
                <tr><th>Valeur de référence</th><td class="value">{format_fr(ref_noise, 1)} HU</td></tr>
                <tr><th>Écart</th><td class="value">{format_fr(deviation, 2, sign=True)} HU</td></tr>
                <tr><th>Critère</th><td>[{format_fr(lower_bound, 2, sign=True)}, {format_fr(upper_bound, 2, sign=True)}] HU</td></tr>
                <tr><th>Statut</th><td>{status_html}</td></tr>
        """

    def _format_nps_stability_html(self, mean_freq: float) -> str:
        """Format NPS frequency stability comparison with reference value."""
        if self._current_device is None or self._current_device.reference_nps_freq is None:
            return ""

        ref_freq = self._current_device.reference_nps_freq
        if ref_freq == 0:
            return ""

        deviation_pct = ((mean_freq - ref_freq) / ref_freq) * 100
        is_conforme = -10.0 <= deviation_pct <= 10.0

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

    def _load_device_config(self, device: DeviceConfig | None):
        """Load device configuration into the UI fields."""
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

        # Re-run analysis display to compare with new reference values
        self._update_results_display()

        self.statusbar.showMessage(f"Installation '{device.display_name()}' enregistrée")

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
            f"Voulez-vous vraiment supprimer l'installation '{self._current_device.display_name()}' ?",
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
            # Device not found - create a new config from DICOM but don't save yet
            self._current_device = None
            # Pre-fill device name from DICOM
            device_name = f"{img.manufacturer or ''} {img.model_name or ''}".strip()
            if device_name:
                self._edit_device_name.setText(device_name)
            # Set combo to "Nouvel équipement"
            self._device_combo.blockSignals(True)
            self._device_combo.setCurrentIndex(0)
            self._device_combo.blockSignals(False)

    def _show_device_manager(self):
        """Show the device management dialog."""
        dialog = DeviceManagerDialog(self._device_db, self)
        dialog.exec()
        # Update device_db reference if it was changed in the dialog
        self._device_db = dialog.device_db
        # Refresh combo after dialog closes (in case devices were modified)
        self._refresh_device_combo()
