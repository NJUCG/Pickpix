"""Custom widget that renders a single preview image and crop overlays."""

from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


def pil_to_qimage(pil_img: Image.Image) -> QImage:
    img = pil_img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
    return qimg.copy()


class PreviewCanvas(QWidget):
    crop_started = Signal(str, int, int)
    crop_dragged = Signal(str, int, int, bool)
    crop_released = Signal(str)
    pan_started = Signal()
    pan_moved = Signal(int, int)
    pan_ended = Signal()
    zoom_requested = Signal(int)

    def __init__(self, method: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.method = method
        self.qimage: QImage | None = None
        self.image_size: tuple[int, int] = (0, 0)
        self.load_error: str | None = None

        self.zoom = 1.0
        self.pan_x = 0
        self.pan_y = 0
        self.base_scale = 1.0
        self.crop_boxes: list[tuple[int, int, int, int]] = []
        self.box_colors: list[str] = []
        self.current_box: tuple[int, int, int, int] | None = None

        self._pan_active = False
        self._crop_active = False
        self._last_pos = QPoint()

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_fixed_view_size(self, width: int, height: int) -> None:
        self.setFixedSize(width, height)

    def set_pil_image(self, pil_img: Image.Image | None, error: str | None = None) -> None:
        self.load_error = error
        if pil_img is None:
            self.qimage = None
            self.image_size = (0, 0)
        else:
            self.qimage = pil_to_qimage(pil_img)
            self.image_size = (pil_img.width, pil_img.height)
        self.update()

    def set_view_state(self, zoom: float, pan_x: int, pan_y: int) -> None:
        self.zoom = zoom
        self.pan_x = pan_x
        self.pan_y = pan_y
        self.update()

    def set_crop_state(self, crop_boxes, box_colors, current_box) -> None:
        self.crop_boxes = list(crop_boxes)
        self.box_colors = list(box_colors)
        self.current_box = current_box
        self.update()

    def _compute_layout(self) -> tuple[float, int, int, int, int]:
        w = self.width()
        h = self.height()
        img_w, img_h = self.image_size
        if img_w <= 0 or img_h <= 0 or w <= 0 or h <= 0:
            return 1.0, 0, 0, 0, 0
        base_scale = min(w / img_w, h / img_h, 1.0)
        scale = base_scale * self.zoom
        self.base_scale = base_scale
        display_w = max(1, int(img_w * scale))
        display_h = max(1, int(img_h * scale))
        x_off = (w - display_w) // 2 + self.pan_x
        y_off = (h - display_h) // 2 + self.pan_y
        return scale, x_off, y_off, display_w, display_h

    def canvas_to_image(self, cx: int, cy: int) -> tuple[int, int] | None:
        scale, x_off, y_off, _, _ = self._compute_layout()
        if scale <= 0:
            return None
        img_w, img_h = self.image_size
        ox = int((cx - x_off) / scale)
        oy = int((cy - y_off) / scale)
        ox = max(0, min(ox, img_w))
        oy = max(0, min(oy, img_h))
        return ox, oy

    def image_to_canvas(self, ix: int, iy: int) -> tuple[int, int]:
        scale, x_off, y_off, _, _ = self._compute_layout()
        return int(ix * scale) + x_off, int(iy * scale) + y_off

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202020"))
        if self.qimage is None or self.qimage.isNull():
            if self.load_error:
                painter.setPen(QColor("#ff8080"))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.load_error)
            painter.end()
            return

        scale, x_off, y_off, display_w, display_h = self._compute_layout()
        if display_w <= 0 or display_h <= 0:
            painter.end()
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(QRect(x_off, y_off, display_w, display_h), self.qimage)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        for idx, (x1, y1, x2, y2) in enumerate(self.crop_boxes):
            color = self.box_colors[idx % len(self.box_colors)] if self.box_colors else "#FF0000"
            painter.setPen(QPen(QColor(color), 3))
            cx1, cy1 = self.image_to_canvas(x1, y1)
            cx2, cy2 = self.image_to_canvas(x2, y2)
            painter.drawRect(QRect(cx1, cy1, cx2 - cx1, cy2 - cy1))

        if self.current_box is not None:
            x1, y1, x2, y2 = self.current_box
            pen = QPen(QColor("yellow"), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            cx1, cy1 = self.image_to_canvas(x1, y1)
            cx2, cy2 = self.image_to_canvas(x2, y2)
            painter.drawRect(QRect(cx1, cy1, cx2 - cx1, cy2 - cy1))
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton:
            coords = self.canvas_to_image(pos.x(), pos.y())
            if coords is None:
                return
            self._crop_active = True
            self.crop_started.emit(self.method, coords[0], coords[1])
        elif event.button() == Qt.MouseButton.RightButton:
            self._pan_active = True
            self._last_pos = pos
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.pan_started.emit()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if self._crop_active and (event.buttons() & Qt.MouseButton.LeftButton):
            coords = self.canvas_to_image(pos.x(), pos.y())
            if coords is None:
                return
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.crop_dragged.emit(self.method, coords[0], coords[1], shift)
        elif self._pan_active and (event.buttons() & Qt.MouseButton.RightButton):
            dx = pos.x() - self._last_pos.x()
            dy = pos.y() - self._last_pos.y()
            self._last_pos = pos
            if dx or dy:
                self.pan_moved.emit(dx, dy)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._crop_active:
            self._crop_active = False
            self.crop_released.emit(self.method)
        elif event.button() == Qt.MouseButton.RightButton and self._pan_active:
            self._pan_active = False
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.pan_ended.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta != 0:
                self.zoom_requested.emit(1 if delta > 0 else -1)
            event.accept()
        else:
            event.ignore()
