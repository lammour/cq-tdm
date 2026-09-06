"""DICOM image viewer widget with windowing and ROI display."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
import numpy as np
from PySide6.QtCore import Qt, Signal, QRectF, QPoint, QTimer
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QBrush, QWheelEvent, QMouseEvent, QCursor, QShowEvent, QResizeEvent
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QStyleOptionSlider,
    QStyle,
    QDialog,
    QPushButton,
)

from ..core.app_config import get_app_config

if TYPE_CHECKING:
    from ..core.dicom_loader import DicomImage


class ToggleSwitch(QWidget):
    """Custom toggle switch widget with graphical knob."""

    toggled = Signal(bool)

    def __init__(self, parent=None, checked: bool = True):
        super().__init__(parent)
        self._checked = checked
        # Proportions: disk diameter, margin
        self._disk_diameter = 14
        self._margin = 3
        # Calculate dimensions
        self._width = 2 * self._disk_diameter + 2 * self._margin
        self._height = self._disk_diameter + 2 * self._margin
        self.setFixedSize(self._width, self._height)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        if self._checked != checked:
            self._checked = checked
            self.update()
            self.toggled.emit(checked)

    def toggle(self):
        self.setChecked(not self._checked)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw pill background
        radius = self._height / 2
        if self._checked:
            bg_color = QColor(76, 175, 80)  # Green (#4caf50)
        else:
            bg_color = QColor(136, 136, 136)  # Gray (#888)

        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, self._width, self._height, radius, radius)

        # Draw knob (white circle)
        knob_color = QColor(255, 255, 255)
        painter.setBrush(QBrush(knob_color))

        # Knob position: left when off, right when on
        knob_y = self._margin
        if self._checked:
            knob_x = self._width - self._margin - self._disk_diameter
        else:
            knob_x = self._margin

        painter.drawEllipse(knob_x, knob_y, self._disk_diameter, self._disk_diameter)
        painter.end()


class MarkedSlider(QSlider):
    """Slider with colored markers to indicate ROI positions.

    Supports interactive dragging of markers:
    - Yellow triangle (UH): drag to move HU analysis slice
    - Green range edges: drag to resize NPS range
    - Green range middle: drag to translate NPS range
    """

    # Signals for marker changes
    water_marker_changed = Signal(int)  # Emitted when water marker is dragged
    nps_range_changed = Signal(int, int)  # Emitted when NPS range is dragged

    # Drag targets
    DRAG_NONE = 0
    DRAG_WATER = 1
    DRAG_NPS_LEFT = 2
    DRAG_NPS_RIGHT = 3
    DRAG_NPS_MIDDLE = 4

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self._water_marker: int | None = None  # Single slice marker (yellow)
        self._nps_range: tuple[int, int] | None = None  # Range markers (green)
        # Set minimum height to accommodate UH label above and SPB band/label below
        self.setMinimumHeight(90)

        # Drag state
        self._drag_target = self.DRAG_NONE
        self._drag_start_x: int = 0
        self._drag_start_nps: tuple[int, int] | None = None

        # Enable mouse tracking for cursor changes
        self.setMouseTracking(True)

    def set_water_marker(self, slice_index: int | None):
        """Set the water ROI marker position."""
        self._water_marker = slice_index
        self.update()

    def set_nps_range(self, start: int | None, end: int | None):
        """Set the NPS ROI range."""
        if start is not None and end is not None:
            self._nps_range = (start, end)
        else:
            self._nps_range = None
        self.update()

    def clear_markers(self):
        """Clear all markers."""
        self._water_marker = None
        self._nps_range = None
        self.update()

    def _get_groove_rect(self) -> QRectF:
        """Get the groove rectangle."""
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        return self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderGroove, self
        )

    def _get_handle_rect(self) -> QRectF:
        """Get the handle rectangle."""
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        return self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderHandle, self
        )

    def _slice_to_x(self, slice_idx: int) -> int:
        """Convert slice index to x coordinate, matching actual handle position."""
        if self.maximum() == self.minimum():
            return 0
        groove = self._get_groove_rect()
        handle = self._get_handle_rect()
        # The handle center moves within a reduced range (accounting for handle width)
        handle_half_width = handle.width() / 2
        effective_left = groove.left() + handle_half_width
        effective_width = groove.width() - handle.width()
        ratio = (slice_idx - self.minimum()) / (self.maximum() - self.minimum())
        return int(effective_left + ratio * effective_width)

    def _x_to_slice(self, x: int) -> int:
        """Convert x coordinate to slice index, matching actual handle position."""
        groove = self._get_groove_rect()
        handle = self._get_handle_rect()
        handle_half_width = handle.width() / 2
        effective_left = groove.left() + handle_half_width
        effective_width = groove.width() - handle.width()
        if effective_width <= 0:
            return self.minimum()
        ratio = (x - effective_left) / effective_width
        ratio = max(0.0, min(1.0, ratio))
        return int(round(self.minimum() + ratio * (self.maximum() - self.minimum())))

    def _hit_test(self, pos: QPoint) -> int:
        """Determine what element is at the given position."""
        x, y = pos.x(), pos.y()
        groove = self._get_groove_rect()
        hit_tolerance = 10  # pixels

        # Check water marker (yellow triangle) - above the groove, higher up
        if self._water_marker is not None:
            water_x = self._slice_to_x(self._water_marker)
            # Hit area is above the groove (y < groove.top())
            if abs(x - water_x) < hit_tolerance and y < groove.top():
                return self.DRAG_WATER

        # Check NPS range (now below the groove)
        if self._nps_range is not None:
            start, end = self._nps_range
            x1 = self._slice_to_x(start)
            x2 = self._slice_to_x(end)
            # NPS band is below groove: band_top = groove.bottom() + 12, band_height = 8
            band_top = groove.bottom() + 12
            band_bottom = band_top + 8 + 14  # Include SPB label area

            # Check left edge
            if abs(x - x1) < hit_tolerance and band_top - 5 < y < band_bottom:
                return self.DRAG_NPS_LEFT

            # Check right edge
            if abs(x - x2) < hit_tolerance and band_top - 5 < y < band_bottom:
                return self.DRAG_NPS_RIGHT

            # Check middle (for translation)
            if x1 + hit_tolerance < x < x2 - hit_tolerance and band_top - 5 < y < band_bottom:
                return self.DRAG_NPS_MIDDLE

        return self.DRAG_NONE

    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse press - start dragging if on a marker."""
        if event.button() == Qt.MouseButton.LeftButton:
            target = self._hit_test(event.pos())
            if target != self.DRAG_NONE:
                self._drag_target = target
                self._drag_start_x = event.pos().x()
                if self._nps_range:
                    self._drag_start_nps = self._nps_range
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        """Handle mouse move - drag markers or update cursor."""
        if self._drag_target != self.DRAG_NONE:
            x = event.pos().x()
            new_slice = self._x_to_slice(x)

            if self._drag_target == self.DRAG_WATER:
                # Move water marker
                new_slice = max(self.minimum(), min(self.maximum(), new_slice))
                if new_slice != self._water_marker:
                    self._water_marker = new_slice
                    self.update()
                    self.water_marker_changed.emit(new_slice)

            elif self._drag_target == self.DRAG_NPS_LEFT and self._nps_range:
                # Move left edge of NPS range
                _, end = self._nps_range
                new_start = max(self.minimum(), min(end - 1, new_slice))
                if new_start != self._nps_range[0]:
                    self._nps_range = (new_start, end)
                    self.update()
                    self.nps_range_changed.emit(new_start, end)

            elif self._drag_target == self.DRAG_NPS_RIGHT and self._nps_range:
                # Move right edge of NPS range
                start, _ = self._nps_range
                new_end = max(start + 1, min(self.maximum(), new_slice))
                if new_end != self._nps_range[1]:
                    self._nps_range = (start, new_end)
                    self.update()
                    self.nps_range_changed.emit(start, new_end)

            elif self._drag_target == self.DRAG_NPS_MIDDLE and self._drag_start_nps:
                # Translate entire NPS range
                delta_x = x - self._drag_start_x
                groove = self._get_groove_rect()
                if groove.width() > 0:
                    delta_slices = int(round(delta_x / groove.width() * (self.maximum() - self.minimum())))
                    orig_start, orig_end = self._drag_start_nps
                    range_size = orig_end - orig_start

                    new_start = orig_start + delta_slices
                    new_end = orig_end + delta_slices

                    # Clamp to valid range
                    if new_start < self.minimum():
                        new_start = self.minimum()
                        new_end = new_start + range_size
                    if new_end > self.maximum():
                        new_end = self.maximum()
                        new_start = new_end - range_size

                    if (new_start, new_end) != self._nps_range:
                        self._nps_range = (new_start, new_end)
                        self.update()
                        self.nps_range_changed.emit(new_start, new_end)

            event.accept()
            return

        # Update cursor based on what's under the mouse
        target = self._hit_test(event.pos())
        if target == self.DRAG_NPS_LEFT or target == self.DRAG_NPS_RIGHT:
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        elif target == self.DRAG_NPS_MIDDLE:
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        elif target == self.DRAG_WATER:
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release - end dragging."""
        if event.button() == Qt.MouseButton.LeftButton and self._drag_target != self.DRAG_NONE:
            self._drag_target = self.DRAG_NONE
            self._drag_start_nps = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        """Reset cursor when leaving the widget."""
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        super().leaveEvent(event)

    def paintEvent(self, event):
        """Paint the slider with markers."""
        super().paintEvent(event)

        if self.maximum() == self.minimum():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        groove_rect = self._get_groove_rect()

        # Draw NPS range (green band) - below the groove to avoid slider handle
        if self._nps_range is not None:
            start, end = self._nps_range
            if start <= self.maximum() and end >= self.minimum():
                x1 = self._slice_to_x(max(start, self.minimum()))
                x2 = self._slice_to_x(min(end, self.maximum()))
                # Draw band below the groove with more distance
                band_top = groove_rect.bottom() + 12
                band_height = 8
                painter.fillRect(
                    x1, band_top,
                    x2 - x1, band_height,
                    QColor(0, 200, 0, 120)
                )
                # Draw edge lines (thicker for better drag target)
                pen = QPen(QColor(0, 200, 0), 3)
                painter.setPen(pen)
                painter.drawLine(x1, band_top - 2, x1, band_top + band_height + 2)
                painter.drawLine(x2, band_top - 2, x2, band_top + band_height + 2)
                # Draw "SPB" label below the band
                font = painter.font()
                font.setPointSize(9)
                font.setBold(True)
                painter.setFont(font)
                painter.setPen(QColor(0, 200, 0))
                label_x = (x1 + x2) // 2 - 12
                painter.drawText(label_x, band_top + band_height + 14, "SPB")

        # Draw water marker (yellow triangle) - above the groove, higher up
        if self._water_marker is not None and self.minimum() <= self._water_marker <= self.maximum():
            x = self._slice_to_x(self._water_marker)
            painter.setPen(QPen(QColor(255, 200, 0), 2))
            painter.setBrush(QColor(255, 200, 0))
            # Draw triangle marker well above groove to avoid slider handle
            triangle_size = 7
            y = groove_rect.top() - 18  # Higher up to avoid slider handle
            painter.drawPolygon([
                QPoint(x, y + triangle_size),  # Bottom point (pointing down)
                QPoint(x - triangle_size, y - triangle_size),  # Top left
                QPoint(x + triangle_size, y - triangle_size),  # Top right
            ])
            # Draw "UH" label above the triangle
            font = painter.font()
            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(255, 200, 0))
            painter.drawText(x - 8, y - triangle_size - 4, "UH")

        painter.end()


@dataclass
class ROI:
    """Region of Interest definition."""

    center_x: float  # Center X in pixels
    center_y: float  # Center Y in pixels
    radius: float  # Radius in pixels (or half-side for square)
    name: str = ""
    color: QColor = None
    is_square: bool = False  # If True, draw as square instead of circle

    def __post_init__(self):
        if self.color is None:
            self.color = QColor(255, 255, 0)  # Yellow default


class ImageViewer(QGraphicsView):
    """Interactive DICOM image viewer with windowing and ROI display."""

    # Signals
    image_loaded = Signal()
    window_changed = Signal(int, int)  # window, level
    zoom_changed = Signal(int)  # zoom percentage

    # Zoom limits
    MIN_ZOOM = 0.1  # 10%
    MAX_ZOOM = 50.0  # 5000%

    def __init__(self, parent=None):
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._current_zoom = 1.0  # Track current zoom level
        self._auto_fit = True  # Auto-fit on resize until user manually zooms

        self._pixmap_item: QGraphicsPixmapItem | None = None

        # ROI management - separate categories
        self._water_rois: list[ROI] = []
        self._water_roi_items: list = []
        self._water_rois_visible: bool = True
        self._water_roi_slice: int | None = None  # Slice index for water ROIs

        self._nps_rois: list[ROI] = []
        self._nps_roi_items: list = []
        self._nps_rois_visible: bool = True
        self._nps_roi_slices: tuple[int, int] | None = None  # (start, end) slice range for NPS

        # Debug overlay (phantom center + perimeter)
        self._debug_mode: bool = False
        self._debug_center: tuple[int, int] | None = None  # (row, col)
        self._debug_radius: float | None = None  # phantom radius in pixels
        self._debug_items: list = []

        self._current_slice: int = 0

        self._image: DicomImage | None = None
        self._window: int = 400  # Soft tissues window
        self._level: int = 40  # Soft tissues level

        # Mouse interaction state
        self._is_adjusting_wl: bool = False
        self._last_mouse_pos: QPoint | None = None
        self._wl_sensitivity: float = 1.0  # Sensitivity multiplier

        # Pending fit flag - for delayed fit after widget is shown
        self._pending_fit: bool = False

        # Setup view
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        if get_app_config().theme == "light":
            self.setBackgroundBrush(QColor(240, 240, 240))
        else:
            self.setBackgroundBrush(QColor(30, 30, 30))

        # Placeholder text
        self._placeholder = self._scene.addText(
            "Aucune image chargée\n\nGlissez-déposez des fichiers DICOM\nou utilisez Fichier → Ouvrir"
        )
        self._placeholder.setDefaultTextColor(QColor(136, 136, 136))

    @property
    def has_image(self) -> bool:
        return self._image is not None

    @property
    def window(self) -> int:
        return self._window

    @property
    def level(self) -> int:
        return self._level

    @property
    def current_image(self) -> DicomImage | None:
        return self._image

    def set_image(self, image: DicomImage, fit_to_view: bool = False):
        """Set the DICOM image to display."""
        self._image = image
        self._update_display()
        if fit_to_view:
            # Schedule fit for after layout is complete
            self._pending_fit = True
            QTimer.singleShot(0, self._do_pending_fit)
        self.image_loaded.emit()

    def _do_pending_fit(self):
        """Execute pending fit to view."""
        if self._pending_fit and self._pixmap_item:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._pending_fit = False
            self._update_zoom_from_transform()

    def showEvent(self, event: QShowEvent):
        """Handle show event to fit image if pending."""
        super().showEvent(event)
        if self._pending_fit:
            QTimer.singleShot(0, self._do_pending_fit)

    def resizeEvent(self, event: QResizeEvent):
        """Handle resize event to keep image fitted (only if auto-fit enabled)."""
        super().resizeEvent(event)
        if self._pixmap_item and not self._pending_fit and self._auto_fit:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._update_zoom_from_transform()

    def _update_zoom_from_transform(self):
        """Update zoom tracking from current transform."""
        transform = self.transform()
        # Get scale factor from transform matrix
        self._current_zoom = transform.m11()  # Horizontal scale factor
        self.zoom_changed.emit(int(self._current_zoom * 100))

    def set_window_level(self, window: int, level: int):
        """Set window and level values."""
        self._window = max(1, window)
        self._level = level
        if self._image is not None:
            self._update_display()
        self.window_changed.emit(self._window, self._level)

    def set_window(self, window: int):
        """Set window width."""
        self.set_window_level(window, self._level)

    def set_level(self, level: int):
        """Set window level (center)."""
        self.set_window_level(self._window, level)

    def reset_view(self):
        """Reset zoom and pan to fit image, re-enable auto-fit."""
        self._auto_fit = True  # Re-enable auto-fit on resize
        if self._pixmap_item:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._update_zoom_from_transform()

    def reset_window_level(self):
        """Reset window/level to default values (Soft tissues)."""
        self.set_window_level(400, 40)

    def set_current_slice_index(self, index: int):
        """Set current slice index and update ROI visibility accordingly."""
        self._current_slice = index
        self._update_roi_visibility()

    def _update_roi_visibility(self):
        """Update ROI visibility based on current slice."""
        # Update water ROIs visibility
        self._clear_roi_items(self._water_roi_items)
        if self._water_rois_visible and self._water_rois:
            if self._water_roi_slice is None or self._water_roi_slice == self._current_slice:
                self._draw_rois(self._water_rois, self._water_roi_items)

        # Update NPS ROIs visibility
        self._clear_roi_items(self._nps_roi_items)
        if self._nps_rois_visible and self._nps_rois:
            if self._nps_roi_slices is None:
                self._draw_rois(self._nps_rois, self._nps_roi_items)
            else:
                start, end = self._nps_roi_slices
                if start <= self._current_slice <= end:
                    self._draw_rois(self._nps_rois, self._nps_roi_items)

    # Water phantom ROI methods
    def set_water_rois(self, rois: list[ROI], slice_index: int | None = None):
        """Set water phantom ROIs (central + peripheral)."""
        self._clear_roi_items(self._water_roi_items)
        self._water_rois = rois
        self._water_roi_slice = slice_index
        self._update_roi_visibility()

    def get_water_roi_slice(self) -> int | None:
        """Get the slice index where water ROIs are defined."""
        return self._water_roi_slice

    def clear_water_rois(self):
        """Clear water phantom ROIs."""
        self._clear_roi_items(self._water_roi_items)
        self._water_rois.clear()
        self._water_roi_slice = None

    def set_water_rois_visible(self, visible: bool):
        """Set visibility of water phantom ROIs."""
        self._water_rois_visible = visible
        self._update_roi_visibility()

    def toggle_water_rois(self) -> bool:
        """Toggle water phantom ROI visibility. Returns new state."""
        self.set_water_rois_visible(not self._water_rois_visible)
        return self._water_rois_visible

    # NPS ROI methods
    def set_nps_rois(self, rois: list[ROI], slice_range: tuple[int, int] | None = None):
        """Set NPS ROIs with optional slice range (start, end inclusive)."""
        self._clear_roi_items(self._nps_roi_items)
        self._nps_rois = rois
        self._nps_roi_slices = slice_range
        self._update_roi_visibility()

    def get_nps_roi_slices(self) -> tuple[int, int] | None:
        """Get the slice range where NPS ROIs are defined."""
        return self._nps_roi_slices

    def clear_nps_rois(self):
        """Clear NPS ROIs."""
        self._clear_roi_items(self._nps_roi_items)
        self._nps_rois.clear()
        self._nps_roi_slices = None

    def set_nps_rois_visible(self, visible: bool):
        """Set visibility of NPS ROIs."""
        self._nps_rois_visible = visible
        self._update_roi_visibility()

    def toggle_nps_rois(self) -> bool:
        """Toggle NPS ROI visibility. Returns new state."""
        self.set_nps_rois_visible(not self._nps_rois_visible)
        return self._nps_rois_visible

    # Debug overlay methods
    def set_debug_mode(self, enabled: bool):
        """Enable or disable debug overlay (phantom center + perimeter)."""
        self._debug_mode = enabled
        self._update_debug_overlay()

    def set_debug_phantom(self, center: tuple[int, int] | None, radius: float | None):
        """Set the detected phantom center and radius for debug display.

        Args:
            center: (row, col) phantom center in pixels, or None to clear
            radius: phantom radius in pixels, or None to clear
        """
        self._debug_center = center
        self._debug_radius = radius
        if self._debug_mode:
            self._update_debug_overlay()

    def _update_debug_overlay(self):
        """Update the debug overlay display."""
        # Clear existing debug items
        for item in self._debug_items:
            self._scene.removeItem(item)
        self._debug_items.clear()

        if not self._debug_mode or self._debug_center is None:
            return

        center_row, center_col = self._debug_center

        # Draw center crosshair (magenta)
        pen = QPen(QColor(255, 0, 255), 2)
        pen.setCosmetic(True)
        crosshair_size = 15

        # Horizontal line
        h_line = self._scene.addLine(
            center_col - crosshair_size, center_row,
            center_col + crosshair_size, center_row,
            pen
        )
        h_line.setZValue(15)
        self._debug_items.append(h_line)

        # Vertical line
        v_line = self._scene.addLine(
            center_col, center_row - crosshair_size,
            center_col, center_row + crosshair_size,
            pen
        )
        v_line.setZValue(15)
        self._debug_items.append(v_line)

        # Draw center point
        point_radius = 3
        point = self._scene.addEllipse(
            center_col - point_radius, center_row - point_radius,
            point_radius * 2, point_radius * 2,
            pen, QBrush(QColor(255, 0, 255))
        )
        point.setZValue(15)
        self._debug_items.append(point)

        # Draw perimeter circle if radius is set
        if self._debug_radius is not None:
            perimeter_pen = QPen(QColor(255, 0, 255), 2, Qt.PenStyle.DashLine)
            perimeter_pen.setCosmetic(True)
            perimeter = self._scene.addEllipse(
                center_col - self._debug_radius,
                center_row - self._debug_radius,
                self._debug_radius * 2,
                self._debug_radius * 2,
                perimeter_pen
            )
            perimeter.setZValue(14)
            self._debug_items.append(perimeter)

            # Add label
            label = self._scene.addText("Phantom")
            label.setDefaultTextColor(QColor(255, 0, 255))
            label.setPos(center_col + self._debug_radius + 5, center_row - 10)
            label.setZValue(15)
            self._debug_items.append(label)

    # Legacy compatibility
    def set_rois(self, rois: list[ROI]):
        """Set ROIs (legacy - uses water phantom category)."""
        self.set_water_rois(rois)

    def clear_rois(self):
        """Clear all ROIs."""
        self.clear_water_rois()
        self.clear_nps_rois()

    def _clear_roi_items(self, items: list):
        """Remove ROI items from scene."""
        for item in items:
            self._scene.removeItem(item)
        items.clear()

    def _draw_rois(self, rois: list[ROI], items: list):
        """Draw ROIs and store items in provided list."""
        for roi in rois:
            self._draw_roi(roi, items)

    def _draw_roi(self, roi: ROI, items: list):
        """Draw a single ROI on the scene."""
        pen = QPen(roi.color, 2)
        pen.setCosmetic(True)  # Constant width regardless of zoom

        # Create shape (circle or square)
        rect = QRectF(
            roi.center_x - roi.radius,
            roi.center_y - roi.radius,
            roi.radius * 2,
            roi.radius * 2,
        )

        if roi.is_square:
            shape = self._scene.addRect(rect, pen)
        else:
            shape = self._scene.addEllipse(rect, pen)

        shape.setZValue(10)  # Above image
        items.append(shape)

        # Add label if name provided
        if roi.name:
            text = self._scene.addText(roi.name)
            text.setDefaultTextColor(roi.color)
            text.setPos(roi.center_x + roi.radius + 2, roi.center_y - 10)
            text.setZValue(11)
            items.append(text)

    def _update_display(self):
        """Update the displayed image with current window/level."""
        if self._image is None:
            return

        # Hide placeholder
        self._placeholder.setVisible(False)

        # Apply window/level
        img_array = self._apply_window_level(
            self._image.pixel_array, self._window, self._level
        )

        # Convert to QImage
        qimage = self._array_to_qimage(img_array)

        # Update or create pixmap item
        pixmap = QPixmap.fromImage(qimage)
        first_image = self._pixmap_item is None
        if first_image:
            self._pixmap_item = self._scene.addPixmap(pixmap)
            self._pixmap_item.setZValue(0)
        else:
            self._pixmap_item.setPixmap(pixmap)

        # Always fit to view on first load
        if first_image:
            self.reset_view()

    def _apply_window_level(
        self, array: np.ndarray, window: int, level: int
    ) -> np.ndarray:
        """Apply window/level to convert HU to display values (0-255)."""
        min_val = level - window / 2
        max_val = level + window / 2

        # Clip and normalize
        result = np.clip(array, min_val, max_val)
        result = ((result - min_val) / (max_val - min_val) * 255).astype(np.uint8)

        return result

    def _array_to_qimage(self, array: np.ndarray) -> QImage:
        """Convert numpy array to QImage."""
        height, width = array.shape
        bytes_per_line = width

        return QImage(
            array.tobytes(),
            width,
            height,
            bytes_per_line,
            QImage.Format.Format_Grayscale8,
        ).copy()  # Copy to ensure data persists

    def wheelEvent(self, event: QWheelEvent):
        """Handle mouse wheel for zooming."""
        factor = 1.15
        if event.angleDelta().y() > 0:
            new_zoom = self._current_zoom * factor
            if new_zoom <= self.MAX_ZOOM:
                self._auto_fit = False  # Disable auto-fit when user manually zooms
                self.scale(factor, factor)
                self._current_zoom = new_zoom
                self.zoom_changed.emit(int(self._current_zoom * 100))
        else:
            new_zoom = self._current_zoom / factor
            if new_zoom >= self.MIN_ZOOM:
                self._auto_fit = False  # Disable auto-fit when user manually zooms
                self.scale(1 / factor, 1 / factor)
                self._current_zoom = new_zoom
                self.zoom_changed.emit(int(self._current_zoom * 100))

    def get_zoom_percent(self) -> int:
        """Get current zoom level as percentage."""
        return int(self._current_zoom * 100)

    def set_zoom_percent(self, percent: int):
        """Set zoom level from percentage."""
        new_zoom = percent / 100.0
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, new_zoom))
        if abs(new_zoom - self._current_zoom) > 0.001:
            self._auto_fit = False  # Disable auto-fit when user manually zooms
            factor = new_zoom / self._current_zoom
            self.scale(factor, factor)
            self._current_zoom = new_zoom
            self.zoom_changed.emit(int(self._current_zoom * 100))

    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse press - right click starts window/level adjustment."""
        if event.button() == Qt.MouseButton.RightButton and self._image is not None:
            self._is_adjusting_wl = True
            self._last_mouse_pos = event.pos()
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        """Handle mouse move - adjust window/level when right-dragging."""
        if self._is_adjusting_wl and self._last_mouse_pos is not None:
            delta = event.pos() - self._last_mouse_pos
            self._last_mouse_pos = event.pos()

            # Horizontal movement adjusts window (contrast)
            # Vertical movement adjusts level (brightness)
            new_window = self._window + int(delta.x() * self._wl_sensitivity * 2)
            new_level = self._level - int(delta.y() * self._wl_sensitivity * 2)

            # Clamp values
            new_window = max(1, min(4000, new_window))
            new_level = max(-1000, min(3000, new_level))

            self.set_window_level(new_window, new_level)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release - end window/level adjustment."""
        if event.button() == Qt.MouseButton.RightButton:
            self._is_adjusting_wl = False
            self._last_mouse_pos = None
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class ImageViewerWidget(QWidget):
    """Complete image viewer with window/level controls and slice navigation."""

    image_loaded = Signal()
    slice_changed = Signal(int)  # Emitted when slice slider changes
    hu_slice_changed = Signal(int)  # Emitted when HU analysis slice changes (0-based)
    nps_range_changed = Signal(int, int)  # Emitted when NPS range changes (0-based)
    open_folder_requested = Signal()  # Emitted when user clicks open folder button

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Slice navigation bar (top)
        slice_bar = QWidget()
        slice_layout = QHBoxLayout(slice_bar)
        slice_layout.setContentsMargins(5, 38, 5, 5)  # Extra top margin for UH label

        slice_layout.addWidget(QLabel("Coupe:"))
        self.slice_slider = MarkedSlider(Qt.Orientation.Horizontal)
        self.slice_slider.setRange(0, 0)
        self.slice_slider.setEnabled(False)
        self.slice_slider.valueChanged.connect(self._on_slice_slider_changed)
        # Connect marker drag signals
        self.slice_slider.water_marker_changed.connect(self._on_water_marker_dragged)
        self.slice_slider.nps_range_changed.connect(self._on_nps_range_dragged)
        slice_layout.addWidget(self.slice_slider, 1)

        self.slice_label = QLabel("0 / 0")
        self.slice_label.setFixedWidth(70)  # Fixed width to prevent layout shifts
        self.slice_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slice_layout.addWidget(self.slice_label)

        layout.addWidget(slice_bar)

        # Container for viewer and welcome overlay
        from PySide6.QtWidgets import QStackedWidget, QPushButton
        self.viewer_stack = QStackedWidget()

        # Welcome screen (shown when no image loaded)
        self.welcome_widget = QWidget()
        is_light = get_app_config().theme == "light"
        self.welcome_widget.setStyleSheet(f"background-color: {'#f0f0f0' if is_light else '#1e1e1e'};")
        welcome_layout = QVBoxLayout(self.welcome_widget)
        welcome_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        welcome_label = QLabel("Aucune image chargée")
        welcome_label.setStyleSheet("color: #888; font-size: 18px; font-weight: bold;")
        welcome_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(welcome_label)

        welcome_layout.addSpacing(20)

        btn_open_folder = QPushButton("Ouvrir un dossier DICOM...")
        btn_open_folder.setStyleSheet("""
            QPushButton {
                padding: 12px 24px;
                font-size: 14px;
                background-color: #0d47a1;
                color: white;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #1565c0;
            }
            QPushButton:pressed {
                background-color: #0a3d91;
            }
        """)
        btn_open_folder.clicked.connect(self.open_folder_requested.emit)
        welcome_layout.addWidget(btn_open_folder, alignment=Qt.AlignmentFlag.AlignCenter)

        welcome_layout.addSpacing(10)

        hint_label = QLabel("ou glissez-déposez un dossier DICOM dans la fenêtre")
        hint_label.setStyleSheet("color: #666; font-size: 11px;")
        hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(hint_label)

        self.viewer_stack.addWidget(self.welcome_widget)

        # Image viewer
        self.viewer = ImageViewer()
        self.viewer.image_loaded.connect(self._on_image_loaded)
        self.viewer.window_changed.connect(self._on_window_level_changed)
        self.viewer.zoom_changed.connect(self._on_zoom_changed)
        self.viewer_stack.addWidget(self.viewer)

        # Start with welcome screen
        self.viewer_stack.setCurrentWidget(self.welcome_widget)

        layout.addWidget(self.viewer_stack, 1)

        # Row 1: Window/Level controls
        wl_controls = QWidget()
        wl_layout = QHBoxLayout(wl_controls)
        wl_layout.setContentsMargins(5, 5, 5, 2)

        # Reset W/L button
        btn_reset_wl = QPushButton("⟲ Réinit. W/L")
        btn_reset_wl.setToolTip("Réinitialiser fenêtrage (Tissus mous)")
        btn_reset_wl.clicked.connect(self._reset_window_level)
        wl_layout.addWidget(btn_reset_wl)

        wl_layout.addSpacing(10)

        # Window control (default: Soft tissues W:400)
        wl_layout.addWidget(QLabel("W:"))
        self.window_spin = QSpinBox()
        self.window_spin.setRange(1, 4000)
        self.window_spin.setValue(400)
        self.window_spin.valueChanged.connect(self.viewer.set_window)
        wl_layout.addWidget(self.window_spin)

        self.window_slider = QSlider(Qt.Orientation.Horizontal)
        self.window_slider.setRange(1, 4000)
        self.window_slider.setValue(400)
        self.window_slider.valueChanged.connect(self.window_spin.setValue)
        self.window_spin.valueChanged.connect(self.window_slider.setValue)
        wl_layout.addWidget(self.window_slider)

        # Level control (default: Soft tissues L:40)
        wl_layout.addWidget(QLabel("L:"))
        self.level_spin = QSpinBox()
        self.level_spin.setRange(-1000, 3000)
        self.level_spin.setValue(40)
        self.level_spin.valueChanged.connect(self.viewer.set_level)
        wl_layout.addWidget(self.level_spin)

        self.level_slider = QSlider(Qt.Orientation.Horizontal)
        self.level_slider.setRange(-1000, 3000)
        self.level_slider.setValue(40)
        self.level_slider.valueChanged.connect(self.level_spin.setValue)
        self.level_spin.valueChanged.connect(self.level_slider.setValue)
        wl_layout.addWidget(self.level_slider)

        layout.addWidget(wl_controls)

        # Row 2: Zoom controls
        zoom_controls = QWidget()
        zoom_layout = QHBoxLayout(zoom_controls)
        zoom_layout.setContentsMargins(5, 2, 5, 5)

        # Reset Zoom button
        btn_reset_zoom = QPushButton("⟲ Réinit. Zoom")
        btn_reset_zoom.setToolTip("Ajuster l'image à la vue")
        btn_reset_zoom.clicked.connect(self._reset_zoom)
        zoom_layout.addWidget(btn_reset_zoom)

        zoom_layout.addSpacing(10)

        # Zoom control
        zoom_layout.addWidget(QLabel("Zoom:"))
        self.zoom_spin = QSpinBox()
        self.zoom_spin.setRange(10, 5000)
        self.zoom_spin.setValue(100)
        self.zoom_spin.setSuffix(" %")
        self.zoom_spin.valueChanged.connect(self._on_zoom_spin_changed)
        zoom_layout.addWidget(self.zoom_spin)

        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(10, 1000)  # Slider limited to 1000% for usability
        self.zoom_slider.setValue(100)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider_changed)
        zoom_layout.addWidget(self.zoom_slider)

        zoom_layout.addStretch()

        layout.addWidget(zoom_controls)

        # ROI toggle overlay (bottom-right corner of image)
        self._create_roi_overlay()

        # Slice selection controls (used by MainWindow)
        self._setup_slice_controls()

    def _create_roi_overlay(self):
        """Create ROI toggle switches overlay on the image viewer."""
        # Create overlay container as child of self (not viewer_stack)
        self.roi_overlay = QWidget(self)
        if get_app_config().theme == "light":
            self.roi_overlay.setStyleSheet("background-color: rgba(240, 240, 240, 200); border-radius: 8px;")
        else:
            self.roi_overlay.setStyleSheet("background-color: rgba(30, 30, 30, 200); border-radius: 8px;")

        overlay_layout = QVBoxLayout(self.roi_overlay)
        overlay_layout.setContentsMargins(10, 8, 10, 8)
        overlay_layout.setSpacing(6)

        # Title label
        title_label = QLabel("Afficher/Cacher :")
        title_label.setStyleSheet("color: #ccc; background: transparent;")
        overlay_layout.addWidget(title_label)

        # UH ROI toggle row
        uh_row = QWidget()
        uh_row.setStyleSheet("background: transparent;")
        uh_layout = QHBoxLayout(uh_row)
        uh_layout.setContentsMargins(0, 0, 0, 0)
        uh_layout.setSpacing(4)

        uh_layout.addStretch()

        uh_label = QLabel("UH")
        uh_label.setStyleSheet("color: #ffcc00; font-weight: bold;")
        uh_layout.addWidget(uh_label)

        self.toggle_water_rois = ToggleSwitch(checked=True)
        self.toggle_water_rois.setToolTip("Afficher/masquer les ROI UH (U)")
        self.toggle_water_rois.toggled.connect(self._on_water_toggle)
        uh_layout.addWidget(self.toggle_water_rois)

        overlay_layout.addWidget(uh_row)

        # SPB ROI toggle row
        spb_row = QWidget()
        spb_row.setStyleSheet("background: transparent;")
        spb_layout = QHBoxLayout(spb_row)
        spb_layout.setContentsMargins(0, 0, 0, 0)
        spb_layout.setSpacing(4)

        spb_layout.addStretch()

        spb_label = QLabel("SPB")
        spb_label.setStyleSheet("color: #00cc00; font-weight: bold;")
        spb_layout.addWidget(spb_label)

        self.toggle_nps_rois = ToggleSwitch(checked=True)
        self.toggle_nps_rois.setToolTip("Afficher/masquer les ROI SPB (S)")
        self.toggle_nps_rois.toggled.connect(self._on_nps_toggle)
        spb_layout.addWidget(self.toggle_nps_rois)

        overlay_layout.addWidget(spb_row)

        # Set fixed size, initially hidden until image loaded
        self.roi_overlay.adjustSize()
        self.roi_overlay.hide()

    def resizeEvent(self, event):
        """Handle resize to reposition ROI overlay."""
        super().resizeEvent(event)
        self._update_roi_overlay_position()

    def showEvent(self, event):
        """Handle show event to position overlay."""
        super().showEvent(event)
        QTimer.singleShot(0, self._update_roi_overlay_position)

    def _update_roi_overlay_position(self):
        """Position the ROI overlay in bottom-right corner of viewer."""
        if hasattr(self, 'roi_overlay') and hasattr(self, 'viewer_stack'):
            # Get the viewer_stack geometry relative to self
            stack_geo = self.viewer_stack.geometry()
            overlay_size = self.roi_overlay.sizeHint()
            # Position in bottom-right corner of viewer_stack with margin
            margin = 15
            x = stack_geo.x() + stack_geo.width() - overlay_size.width() - margin
            y = stack_geo.y() + stack_geo.height() - overlay_size.height() - margin
            self.roi_overlay.move(x, y)
            self.roi_overlay.raise_()

    def _setup_slice_controls(self):
        """Set up slice selection controls for analysis (used by MainWindow)."""
        # Slice selection controls for analysis (stored but not added to layout - used by MainWindow)
        self.slice_controls_widget = QWidget()
        slice_controls_layout = QHBoxLayout(self.slice_controls_widget)
        slice_controls_layout.setContentsMargins(0, 0, 0, 0)

        # HU analysis slice
        slice_controls_layout.addWidget(QLabel("Coupe UH:"))
        self.hu_slice_spin = QSpinBox()
        self.hu_slice_spin.setRange(1, 1)
        self.hu_slice_spin.setValue(1)
        self.hu_slice_spin.setToolTip("Coupe pour l'analyse UH (nombre CT, uniformité, bruit)")
        self.hu_slice_spin.setMinimumWidth(60)
        slice_controls_layout.addWidget(self.hu_slice_spin)

        btn_set_hu = QPushButton("↓")
        btn_set_hu.setFixedWidth(24)
        btn_set_hu.setToolTip("Définir à la position courante")
        btn_set_hu.clicked.connect(self._set_hu_slice_from_current)
        slice_controls_layout.addWidget(btn_set_hu)

        slice_controls_layout.addSpacing(15)

        # NPS analysis range
        slice_controls_layout.addWidget(QLabel("Coupes SPB:"))
        self.nps_start_spin = QSpinBox()
        self.nps_start_spin.setRange(1, 1)
        self.nps_start_spin.setValue(1)
        self.nps_start_spin.setToolTip("Première coupe pour l'analyse SPB")
        self.nps_start_spin.setMinimumWidth(60)
        slice_controls_layout.addWidget(self.nps_start_spin)

        slice_controls_layout.addWidget(QLabel("→"))

        self.nps_end_spin = QSpinBox()
        self.nps_end_spin.setRange(1, 1)
        self.nps_end_spin.setValue(1)
        self.nps_end_spin.setToolTip("Dernière coupe pour l'analyse SPB")
        self.nps_end_spin.setMinimumWidth(60)
        slice_controls_layout.addWidget(self.nps_end_spin)

        btn_set_nps = QPushButton("↓")
        btn_set_nps.setFixedWidth(24)
        btn_set_nps.setToolTip("Centrer sur la position courante (10 coupes)")
        btn_set_nps.clicked.connect(self._set_nps_range_from_current)
        slice_controls_layout.addWidget(btn_set_nps)

        slice_controls_layout.addStretch()

        # Connect spinboxes to update markers
        self.hu_slice_spin.valueChanged.connect(self._on_hu_slice_changed)
        self.nps_start_spin.valueChanged.connect(self._on_nps_range_changed)
        self.nps_end_spin.valueChanged.connect(self._on_nps_range_changed)

    def set_image(self, image: DicomImage, fit_to_view: bool = False):
        """Set the image to display."""
        self.viewer.set_image(image, fit_to_view=fit_to_view)

    def set_slice_count(self, count: int):
        """Set the number of slices for the slider."""
        # Block signals during setup to avoid triggering analysis before ready
        self.hu_slice_spin.blockSignals(True)
        self.nps_start_spin.blockSignals(True)
        self.nps_end_spin.blockSignals(True)

        if count > 1:
            self.slice_slider.setRange(0, count - 1)
            self.slice_slider.setEnabled(True)
            self._update_slice_label()
            # Update analysis slice spinboxes (1-based display)
            self.hu_slice_spin.setRange(1, count)
            self.nps_start_spin.setRange(1, count)
            self.nps_end_spin.setRange(1, count)
            # Set default NPS range to 10 central slices
            start = max(1, (count - 10) // 2 + 1)
            end = min(count, start + 9)
            self.nps_start_spin.setValue(start)
            self.nps_end_spin.setValue(end)
            # Set HU slice to center
            self.hu_slice_spin.setValue((count + 1) // 2)
            # Update markers to match spinbox values
            self.slice_slider.set_water_marker((count + 1) // 2 - 1)  # 0-based
            self.slice_slider.set_nps_range(start - 1, end - 1)  # 0-based
        else:
            self.slice_slider.setRange(0, 0)
            self.slice_slider.setEnabled(False)
            self.slice_label.setText("1 / 1" if count == 1 else "0 / 0")
            self.hu_slice_spin.setRange(1, 1)
            self.nps_start_spin.setRange(1, 1)
            self.nps_end_spin.setRange(1, 1)
            self.slice_slider.clear_markers()

        self.hu_slice_spin.blockSignals(False)
        self.nps_start_spin.blockSignals(False)
        self.nps_end_spin.blockSignals(False)

    def set_current_slice(self, index: int):
        """Set the current slice index (without emitting signal)."""
        self.slice_slider.blockSignals(True)
        self.slice_slider.setValue(index)
        self.slice_slider.blockSignals(False)
        self._update_slice_label()
        self.viewer.set_current_slice_index(index)

    def _update_slice_label(self):
        """Update the slice counter label."""
        current = self.slice_slider.value() + 1
        total = self.slice_slider.maximum() + 1
        self.slice_label.setText(f"{current} / {total}")

    def _on_slice_slider_changed(self, value: int):
        """Handle slice slider change."""
        self._update_slice_label()
        self.viewer.set_current_slice_index(value)
        self.slice_changed.emit(value)

    def _set_preset(self, window: int, level: int):
        """Set a window/level preset."""
        self.window_spin.setValue(window)
        self.level_spin.setValue(level)

    def _reset_all(self):
        """Reset all view parameters (slice, zoom, window/level, analysis slices)."""
        # Get total slice count
        total_slices = self.slice_slider.maximum() + 1

        if self.slice_slider.isEnabled() and total_slices > 1:
            # Reset to middle slice (default HU position)
            middle_slice = (total_slices - 1) // 2
            self.slice_slider.setValue(middle_slice)

            # Reset HU slice to middle (1-based)
            self.hu_slice_spin.setValue(middle_slice + 1)

            # Reset NPS range to 10 central slices (1-based)
            nps_start = max(1, (total_slices - 10) // 2 + 1)
            nps_end = min(total_slices, nps_start + 9)
            self.nps_start_spin.setValue(nps_start)
            self.nps_end_spin.setValue(nps_end)

        # Reset window/level to Soft tissues (default)
        self._set_preset(400, 40)

        # Reset zoom/pan
        self.viewer.reset_view()

    def _on_water_toggle(self, checked: bool):
        """Handle water ROI toggle switch change."""
        self.viewer.set_water_rois_visible(checked)

    def _on_nps_toggle(self, checked: bool):
        """Handle NPS ROI toggle switch change."""
        self.viewer.set_nps_rois_visible(checked)

    def _toggle_water_rois(self):
        """Toggle water phantom ROI visibility (keyboard shortcut)."""
        self.toggle_water_rois.toggle()

    def _toggle_nps_rois(self):
        """Toggle NPS ROI visibility (keyboard shortcut)."""
        self.toggle_nps_rois.toggle()

    def _reset_window_level(self):
        """Reset window/level to default (Soft tissues)."""
        self._set_preset(400, 40)

    def _reset_zoom(self):
        """Reset zoom to fit image in view."""
        self.viewer.reset_view()

    def _on_zoom_changed(self, percent: int):
        """Handle zoom change from viewer (e.g., mouse wheel)."""
        # Update controls without triggering signals back to viewer
        self.zoom_spin.blockSignals(True)
        self.zoom_slider.blockSignals(True)
        self.zoom_spin.setValue(percent)
        # Clamp slider value to its range
        self.zoom_slider.setValue(min(percent, self.zoom_slider.maximum()))
        self.zoom_spin.blockSignals(False)
        self.zoom_slider.blockSignals(False)

    def _on_zoom_spin_changed(self, value: int):
        """Handle zoom spinbox change."""
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(min(value, self.zoom_slider.maximum()))
        self.zoom_slider.blockSignals(False)
        self.viewer.set_zoom_percent(value)

    def _on_zoom_slider_changed(self, value: int):
        """Handle zoom slider change."""
        self.zoom_spin.blockSignals(True)
        self.zoom_spin.setValue(value)
        self.zoom_spin.blockSignals(False)
        self.viewer.set_zoom_percent(value)

    def _on_window_level_changed(self, window: int, level: int):
        """Handle window/level change from viewer (e.g., mouse drag)."""
        # Update spinboxes without triggering their signals back to viewer
        self.window_spin.blockSignals(True)
        self.level_spin.blockSignals(True)
        self.window_slider.blockSignals(True)
        self.level_slider.blockSignals(True)

        self.window_spin.setValue(window)
        self.level_spin.setValue(level)
        self.window_slider.setValue(window)
        self.level_slider.setValue(level)

        self.window_spin.blockSignals(False)
        self.level_spin.blockSignals(False)
        self.window_slider.blockSignals(False)
        self.level_slider.blockSignals(False)

    def set_water_roi_marker(self, slice_index: int | None):
        """Set the water ROI marker on the slice slider."""
        self.slice_slider.set_water_marker(slice_index)

    def set_nps_roi_markers(self, start: int | None, end: int | None):
        """Set the NPS ROI range markers on the slice slider."""
        self.slice_slider.set_nps_range(start, end)

    def clear_roi_markers(self):
        """Clear all ROI markers from the slice slider."""
        self.slice_slider.clear_markers()

    def _on_image_loaded(self):
        """Handle image loaded signal."""
        # Switch from welcome screen to viewer
        self.viewer_stack.setCurrentWidget(self.viewer)
        # Show ROI overlay
        self.roi_overlay.show()
        self._update_roi_overlay_position()
        self.image_loaded.emit()

    def _set_hu_slice_from_current(self):
        """Set HU analysis slice from current slider position."""
        current = self.slice_slider.value() + 1  # 1-based
        self.hu_slice_spin.setValue(current)

    def _set_nps_range_from_current(self):
        """Set NPS range centered on current slider position (10 slices)."""
        current = self.slice_slider.value()  # 0-based
        total = self.slice_slider.maximum() + 1
        # Center 10 slices around current position
        start = max(0, current - 4)
        end = min(total - 1, start + 9)
        # Adjust start if end was clamped
        start = max(0, end - 9)
        # Convert to 1-based for display
        self.nps_start_spin.setValue(start + 1)
        self.nps_end_spin.setValue(end + 1)

    def get_hu_slice_index(self) -> int:
        """Get the HU analysis slice index (0-based)."""
        return self.hu_slice_spin.value() - 1

    def _clamp_slice(self, slice_index: int) -> int:
        """Clamp a 0-based slice index to the range of the loaded series.

        Saved device slice values may come from a longer series; the spinboxes
        would clamp silently while the raw value was still emitted to the analysis.
        """
        lo = self.hu_slice_spin.minimum() - 1
        hi = self.hu_slice_spin.maximum() - 1
        return max(lo, min(int(slice_index), hi))

    def get_nps_slice_range(self) -> tuple[int, int]:
        """Get the NPS analysis slice range (0-based, inclusive)."""
        return (self.nps_start_spin.value() - 1, self.nps_end_spin.value() - 1)

    def set_hu_slice_index(self, slice_index: int):
        """Set the HU analysis slice index (0-based), clamped to the loaded series."""
        slice_index = self._clamp_slice(slice_index)
        # Block signals to avoid triggering analysis during programmatic change
        self.hu_slice_spin.blockSignals(True)
        self.hu_slice_spin.setValue(slice_index + 1)  # Convert to 1-based
        self.slice_slider.set_water_marker(slice_index)
        self.hu_slice_spin.blockSignals(False)
        # Emit signal to trigger analysis
        self.hu_slice_changed.emit(slice_index)

    def set_nps_slice_range(self, start: int, end: int):
        """Set the NPS analysis slice range (0-based, inclusive), clamped to the series."""
        start = self._clamp_slice(start)
        end = self._clamp_slice(end)
        if end < start:
            start, end = end, start
        # Block signals to avoid triggering analysis during programmatic change
        self.nps_start_spin.blockSignals(True)
        self.nps_end_spin.blockSignals(True)
        self.nps_start_spin.setValue(start + 1)  # Convert to 1-based
        self.nps_end_spin.setValue(end + 1)  # Convert to 1-based
        self.slice_slider.set_nps_range(start, end)
        self.nps_start_spin.blockSignals(False)
        self.nps_end_spin.blockSignals(False)
        # Emit signal to trigger analysis
        self.nps_range_changed.emit(start, end)

    def _on_hu_slice_changed(self, value: int):
        """Handle HU slice spinbox change."""
        slice_index = value - 1  # Convert to 0-based
        # Update marker on slider
        self.slice_slider.set_water_marker(slice_index)
        # Emit signal for analysis
        self.hu_slice_changed.emit(slice_index)

    def _on_nps_range_changed(self):
        """Handle NPS range spinbox change."""
        start = self.nps_start_spin.value() - 1  # 0-based
        end = self.nps_end_spin.value() - 1  # 0-based
        # Update markers on slider
        self.slice_slider.set_nps_range(start, end)
        # Emit signal for analysis
        self.nps_range_changed.emit(start, end)

    def _on_water_marker_dragged(self, slice_index: int):
        """Handle water marker being dragged on the slider."""
        # Update spinbox without triggering another marker update
        self.hu_slice_spin.blockSignals(True)
        self.hu_slice_spin.setValue(slice_index + 1)  # Convert to 1-based
        self.hu_slice_spin.blockSignals(False)
        # Emit signal for analysis
        self.hu_slice_changed.emit(slice_index)

    def _on_nps_range_dragged(self, start: int, end: int):
        """Handle NPS range being dragged on the slider."""
        # Update spinboxes without triggering another marker update
        self.nps_start_spin.blockSignals(True)
        self.nps_end_spin.blockSignals(True)
        self.nps_start_spin.setValue(start + 1)  # Convert to 1-based
        self.nps_end_spin.setValue(end + 1)
        self.nps_start_spin.blockSignals(False)
        self.nps_end_spin.blockSignals(False)
        # Emit signal for analysis
        self.nps_range_changed.emit(start, end)


class ArtifactInspectionDialog(QDialog):
    """Floating dialog for artifact inspection per ANSM requirements.

    Displays the image with artifact window (WC=0, WW=80) and allows
    the user to confirm presence or absence of clinically significant artifacts.
    """

    # Signal emitted when user makes a choice: True = artifacts present, False = absent
    artifact_choice = Signal(bool)

    def __init__(self, images: list[DicomImage], initial_slice: int = 0, parent=None):
        super().__init__(parent)
        self._images = images
        self._current_slice = initial_slice
        self._result: bool | None = None
        self._description: str = ""

        self.setWindowTitle("Inspection des artéfacts")
        self.setModal(True)
        self.setMinimumSize(800, 600)
        self.resize(1200, 900)

        self._setup_ui()
        self._update_image()

    def _setup_ui(self):
        """Set up the dialog UI."""
        is_light = get_app_config().theme == "light"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Slice slider at top (only if multiple images)
        if len(self._images) > 1:
            slice_container = QWidget()
            slice_layout = QHBoxLayout(slice_container)
            slice_layout.setContentsMargins(5, 5, 5, 5)

            slice_layout.addWidget(QLabel("Coupe:"))

            self._slice_slider = QSlider(Qt.Orientation.Horizontal)
            self._slice_slider.setRange(0, len(self._images) - 1)
            self._slice_slider.setValue(self._current_slice)
            self._slice_slider.valueChanged.connect(self._on_slice_changed)
            slice_layout.addWidget(self._slice_slider, 1)

            self._slice_label = QLabel(f"{self._current_slice + 1} / {len(self._images)}")
            self._slice_label.setMinimumWidth(60)
            slice_layout.addWidget(self._slice_label)

            layout.addWidget(slice_container)

        # Image viewer (simplified, artifact window preset)
        self._viewer = ImageViewer()
        self._viewer.set_window_level(80, 0)  # Artifact window: W=80, L=0
        # Disable drag mode to keep image fitted
        self._viewer.setDragMode(QGraphicsView.DragMode.NoDrag)
        layout.addWidget(self._viewer, 1)

        # Window settings info (below image)
        info_label = QLabel("Fenêtre artéfacts ANSM : Centre (L) = 0 UH, Largeur (W) = 80 UH")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_label.setStyleSheet(f"color: {'#666' if is_light else '#aaa'};")
        layout.addWidget(info_label)

        # Button container
        button_container = QWidget()
        button_layout = QHBoxLayout(button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(20)

        # Absence d'artéfacts button (green - conformity)
        btn_absent = QPushButton("Absence d'artéfacts")
        btn_absent.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                font-size: 12px;
                font-weight: bold;
                background-color: #2e7d32;
                color: white;
                border: none;
                border-radius: 4px;
                min-width: 150px;
            }
            QPushButton:hover {
                background-color: #388e3c;
            }
            QPushButton:pressed {
                background-color: #1b5e20;
            }
        """)
        btn_absent.clicked.connect(self._on_absent)
        button_layout.addWidget(btn_absent)

        # Présence d'artéfacts button (orange/red - non-conformity)
        self._btn_present = QPushButton("Présence d'artéfacts")
        self._btn_present.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                font-size: 12px;
                font-weight: bold;
                background-color: #d84315;
                color: white;
                border: none;
                border-radius: 4px;
                min-width: 150px;
            }
            QPushButton:hover {
                background-color: #e64a19;
            }
            QPushButton:pressed {
                background-color: #bf360c;
            }
        """)
        self._btn_present.clicked.connect(self._on_present)
        button_layout.addWidget(self._btn_present)

        layout.addWidget(button_container, alignment=Qt.AlignmentFlag.AlignCenter)

        # Description container (initially hidden)
        self._description_container = QWidget()
        self._description_container.setVisible(False)
        desc_layout = QVBoxLayout(self._description_container)
        desc_layout.setContentsMargins(0, 0, 0, 0)
        desc_layout.setSpacing(8)

        desc_label = QLabel("Description des artéfacts observés :")
        desc_label.setStyleSheet(f"color: {'#222' if is_light else '#fff'}; font-weight: bold; font-size: 12px;")
        desc_layout.addWidget(desc_label)

        from PySide6.QtWidgets import QTextEdit
        self._description_edit = QTextEdit()
        self._description_edit.setPlaceholderText(
            "Décrivez les artéfacts observés (type, localisation, sévérité...)"
        )
        self._description_edit.setMaximumHeight(80)
        if is_light:
            self._description_edit.setStyleSheet("""
                QTextEdit {
                    background-color: #ffffff;
                    color: #222;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                    padding: 6px;
                    font-size: 12px;
                }
            """)
        else:
            self._description_edit.setStyleSheet("""
                QTextEdit {
                    background-color: #2b2b2b;
                    color: #fff;
                    border: 1px solid #555;
                    border-radius: 4px;
                    padding: 6px;
                    font-size: 12px;
                }
            """)
        desc_layout.addWidget(self._description_edit)

        # Confirm button for artifact description
        self._btn_confirm = QPushButton("Confirmer la présence d'artéfacts")
        self._btn_confirm.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                font-size: 12px;
                font-weight: bold;
                background-color: #d84315;
                color: white;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #e64a19;
            }
            QPushButton:pressed {
                background-color: #bf360c;
            }
        """)
        self._btn_confirm.clicked.connect(self._on_confirm_present)
        desc_layout.addWidget(self._btn_confirm, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self._description_container)

        # Set dialog background
        if is_light:
            self.setStyleSheet("QDialog { background-color: #f0f0f0; }")
        else:
            self.setStyleSheet("QDialog { background-color: #1e1e1e; }")

    def _update_image(self):
        """Update the displayed image."""
        if 0 <= self._current_slice < len(self._images):
            self._viewer.set_image(self._images[self._current_slice], fit_to_view=True)

    def _on_slice_changed(self, value: int):
        """Handle slice slider change."""
        self._current_slice = value
        self._slice_label.setText(f"{value + 1} / {len(self._images)}")
        self._update_image()

    def _on_absent(self):
        """Handle 'Absence d'artéfacts' button click."""
        self._result = False
        self._description = ""
        self.artifact_choice.emit(False)
        self.accept()

    def _on_present(self):
        """Handle 'Présence d'artéfacts' button click - show description input."""
        # Show description input
        self._description_container.setVisible(True)
        self._btn_present.setEnabled(False)
        self._description_edit.setFocus()
        # Re-fit image after layout change
        QTimer.singleShot(0, self._fit_image)

    def _on_confirm_present(self):
        """Handle confirmation after description is entered."""
        self._result = True
        self._description = self._description_edit.toPlainText().strip()
        self.artifact_choice.emit(True)
        self.accept()

    @property
    def result(self) -> bool | None:
        """Get the artifact inspection result.

        Returns:
            True if artifacts present (NC), False if absent (Conforme), None if canceled.
        """
        return self._result

    @property
    def description(self) -> str:
        """Get the artifact description (only if artifacts present)."""
        return self._description

    def showEvent(self, event):
        """Handle show event to fit image after dialog is displayed."""
        super().showEvent(event)
        # Fit image after dialog is shown and has proper size
        QTimer.singleShot(0, self._fit_image)

    def resizeEvent(self, event):
        """Handle resize to keep image fitted."""
        super().resizeEvent(event)
        self._fit_image()

    def _fit_image(self):
        """Fit the image to the viewer size."""
        if self._viewer._pixmap_item:
            self._viewer.fitInView(
                self._viewer._pixmap_item,
                Qt.AspectRatioMode.KeepAspectRatio
            )

    @classmethod
    def inspect(cls, images: list[DicomImage], initial_slice: int = 0, parent=None) -> tuple[bool | None, str]:
        """Static method to show dialog and return result.

        Args:
            images: List of DICOM images to inspect.
            initial_slice: Initial slice index to display.
            parent: Parent widget.

        Returns:
            Tuple of (result, description):
            - result: True if artifacts present, False if absent, None if canceled.
            - description: Description of artifacts (empty if absent or canceled).
        """
        dialog = cls(images, initial_slice, parent)
        dialog.show()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.result, dialog.description
        return None, ""
