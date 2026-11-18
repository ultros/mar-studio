"""Photo workspace for editing the pixels of a single image layer."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGraphicsItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from PIL import Image, ImageChops, ImageFilter, ImageOps

from .photo import (PASTE_TOOL, PhotoEdits, PixelClip, PixelClipboard, color_selection, erase_mask,
                    filtered, levels, lift_selection, masked_pixels, move_pixels, paste_pixels, pasted_sources,
                    selection_mask, stroke_points)

LEFT = Qt.MouseButton.LeftButton
RIGHT = Qt.MouseButton.RightButton
MIDDLE = Qt.MouseButton.MiddleButton
ALT = Qt.KeyboardModifier.AltModifier
SELECT_TOOLS = ("Rectangle select", "Freehand select")
PAINT_TOOLS = ("Brush", "Eraser", "Clone stamp")
MOVE_TOOL = "Move selected pixels"
COLOR_TOOL = "Select by color"
CORNERS = ((-1, -1), (1, -1), (1, 1), (-1, 1))


def qt_pixels(image):
    return QImage(image.tobytes(), image.width, image.height, image.width * 4,
                  QImage.Format.Format_RGBA8888).copy()


class PhotoSurface(QGraphicsItem):
    def __init__(self, image):
        super().__init__()
        tile = QPixmap(28, 28)
        tile.fill(QColor("#bec4cb"))
        with QPainter(tile) as painter:
            painter.fillRect(0, 0, 14, 14, QColor("#edf0f2"))
            painter.fillRect(14, 14, 14, 14, QColor("#edf0f2"))
        self.checker = QBrush(tile)
        self.image = qt_pixels(image)

    def boundingRect(self):
        return QRectF(self.image.rect())

    def paint(self, painter, option, widget=None):
        painter.fillRect(self.boundingRect(), self.checker)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(QPointF(), self.image)

    def replace(self, image):
        self.prepareGeometryChange()
        self.image = qt_pixels(image)
        self.update()

    def patch(self, image, box):
        patch = qt_pixels(image.crop(box))
        with QPainter(self.image) as painter:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawImage(box[0], box[1], patch)
        self.update(QRectF(box[0], box[1], patch.width(), patch.height()))


class PhotoView(QGraphicsView):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setObjectName("photoView")
        self.setAccessibleName("Image pixels")
        self.setScene(QGraphicsScene(self))
        self.scene().setBackgroundBrush(QColor("#162332"))
        self.surface = PhotoSurface(editor.edits.image)
        self.scene().addItem(self.surface)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setMouseTracking(True)
        self.pan = None
        self.stroke = None
        self.moving = None
        self.paste_drag = None
        self.sketch = None
        self.hover = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.reframe()

    def reframe(self):
        self.setSceneRect(self.surface.boundingRect().adjusted(-16, -16, 16, 16))

    def fit(self):
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.editor.update_zoom()

    def actual_pixels(self):
        self.resetTransform()
        self.editor.update_zoom()

    def wheelEvent(self, event):
        factor = 1.20 ** (event.angleDelta().y() / 120)
        if 0.01 <= self.transform().m11() * factor <= 32:
            self.scale(factor, factor)
            self.editor.update_zoom()
        event.accept()

    def pixel_position(self, event):
        p = self.mapToScene(event.position().toPoint())
        return round(p.x()), round(p.y())

    def in_image(self, point):
        width, height = self.editor.edits.image.size
        return 0 <= point[0] < width and 0 <= point[1] < height

    def clamp(self, point):
        width, height = self.editor.edits.image.size
        return max(0, min(width - 1, point[0])), max(0, min(height - 1, point[1]))

    def mousePressEvent(self, event):
        editor = self.editor
        viewport_pos = event.position().toPoint()
        if event.button() == MIDDLE:
            self.pan = (viewport_pos, self.horizontalScrollBar().value(), self.verticalScrollBar().value())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() not in (LEFT, RIGHT) or editor.before.isChecked() or editor.cutout_preview.isChecked():
            return
        if editor.floating_clip is not None:
            if event.button() == LEFT:
                self.begin_paste_drag(viewport_pos)
            event.accept()
            return
        point = self.pixel_position(event)
        if not self.in_image(point):
            return
        tool = editor.tool.currentText()
        if event.button() == RIGHT or event.modifiers() & ALT:
            if tool == "Clone stamp":
                editor.clone_anchor = point
                editor.note.setText("Source sampled. Paint where you want to copy those pixels.")
                self.viewport().update()
            elif tool == "Brush" and event.modifiers() & ALT:
                editor.set_color(QColor(*editor.edits.image.getpixel(point)[:3]))
            event.accept()
            return
        if tool in SELECT_TOOLS:
            self.sketch = [point, point]
        elif tool == COLOR_TOOL:
            editor.select_color(point)
        elif tool == MOVE_TOOL:
            if editor.mask is None or not editor.mask.getpixel(point):
                editor.note.setText("Start the drag inside a selection.")
            else:
                lifted = lift_selection(editor.edits.image, editor.mask)
                if lifted is not None:
                    pixels, bounds = lifted
                    self.moving = {
                        "start": point, "bounds": bounds, "pixels": pixels, "preview": qt_pixels(pixels),
                        "remainder": erase_mask(editor.edits.image, editor.mask), "offset": (0, 0),
                    }
        elif tool == "Clone stamp" and editor.clone_anchor is None:
            editor.note.setText("Alt-click or right-click a source pixel first.")
        else:
            offset = None if tool != "Clone stamp" else (
                editor.clone_anchor[0] - point[0], editor.clone_anchor[1] - point[1])
            self.stroke = {
                "tool": tool, "start": point, "last": point, "offset": offset,
                "size": editor.size.value(), "hardness": editor.hardness.value(),
                "opacity": editor.opacity.value(), "color": editor.color.getRgb()[:3],
            }
            editor.edits.begin_stroke()
            self.paint_stroke(point, first=True)
        event.accept()

    def mouseMoveEvent(self, event):
        if self.pan is not None:
            origin, x, y = self.pan
            delta = event.position().toPoint() - origin
            self.horizontalScrollBar().setValue(x - delta.x())
            self.verticalScrollBar().setValue(y - delta.y())
        elif self.paste_drag is not None:
            self.position_paste(event)
        elif self.sketch is not None:
            point = self.clamp(self.pixel_position(event))
            if self.editor.tool.currentText() == "Rectangle select":
                self.sketch[1] = point
            elif point != self.sketch[-1]:
                self.sketch.append(point)
            self.viewport().update()
        elif self.moving is not None:
            offset = self.move_offset(self.pixel_position(event))
            if offset != self.moving["offset"]:
                if offset == (0, 0):
                    self.surface.replace(self.editor.edits.image)
                elif self.moving["offset"] == (0, 0):
                    self.surface.replace(self.moving["remainder"])
                self.moving["offset"] = offset
                self.viewport().update()
        elif self.stroke is not None:
            self.paint_stroke(self.clamp(self.pixel_position(event)))
        else:
            point = self.pixel_position(event)
            hover = point if self.in_image(point) else None
            if hover != self.hover:
                self.hover = hover
                self.viewport().update()
        event.accept()

    def leaveEvent(self, event):
        self.hover = None
        self.viewport().update()
        super().leaveEvent(event)

    def paste_handles(self):
        transform = self.editor.paste_transform()
        rect = self.editor.paste_rect()
        zoom = self.transform().m11()
        corners = {sign: transform.map(point) for sign, point in zip(
            CORNERS, (rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()))}
        top = transform.map(QPointF(rect.center().x(), 0))
        rotate = transform.map(QPointF(rect.center().x(), -24 / zoom))
        return corners, top, rotate

    def begin_paste_drag(self, viewport_pos):
        editor = self.editor
        point = self.mapToScene(viewport_pos)
        transform = editor.paste_transform()
        corners, _, rotate = self.paste_handles()
        center = transform.map(editor.paste_rect().center())
        angle = editor.paste_rotation.value()
        if (self.mapFromScene(rotate) - viewport_pos).manhattanLength() <= 9:
            self.paste_drag = {"mode": "rotate", "center": center, "start": point - center, "angle": angle}
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        for sign, corner in corners.items():
            if (self.mapFromScene(corner) - viewport_pos).manhattanLength() <= 7:
                sx, sy = sign
                self.paste_drag = {"mode": "resize", "anchor": corners[-sx, -sy], "offset": point - corner,
                                   "sign": sign, "angle": angle, "size": (editor.paste_width.value(),
                                                                         editor.paste_height.value())}
                self.setCursor(Qt.CursorShape.SizeFDiagCursor if sx == sy else Qt.CursorShape.SizeBDiagCursor)
                return
        inverse, _ = transform.inverted()
        if editor.paste_rect().contains(inverse.map(point)):
            self.paste_drag = {"mode": "move", "start": point, "position": editor.paste_position}
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def position_paste(self, event):
        editor = self.editor
        drag = self.paste_drag
        point = self.mapToScene(event.position().toPoint())
        if drag["mode"] == "move":
            left, top = drag["position"]
            offset = point - drag["start"]
            editor.set_paste_position(left + round(offset.x()), top + round(offset.y()))
        elif drag["mode"] == "rotate":
            start, current = drag["start"], point - drag["center"]
            delta = math.degrees(math.atan2(current.y(), current.x()) - math.atan2(start.y(), start.x()))
            angle = (drag["angle"] + delta + 180) % 360 - 180
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                angle = round(angle / 15) * 15
            editor.paste_rotation.setValue(angle)
        else:
            editor.resize_from_corner(drag, point)

    def keyPressEvent(self, event):
        if self.editor.floating_clip is not None:
            offsets = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                       Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
            if event.key() in offsets:
                dx, dy = offsets[event.key()]
                x, y = self.editor.paste_position
                step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
                self.editor.set_paste_position(x + dx * step, y + dy * step)
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.editor.place_paste()
                event.accept()
                return
        super().keyPressEvent(event)

    def move_offset(self, point):
        dragging = self.moving
        x, y = dragging["start"]
        left, top, right, bottom = dragging["bounds"]
        width, height = self.editor.edits.image.size
        return max(-left, min(width - right, point[0] - x)), max(-top, min(height - bottom, point[1] - y))

    def paint_stroke(self, point, first=False):
        stroke = self.stroke
        last = stroke["last"]
        if last == point and not first:
            return
        edits = self.editor.edits
        brush = (stroke["tool"], stroke["size"], stroke["hardness"], stroke["opacity"],
                 stroke["color"], self.editor.mask, stroke["offset"])
        area = None
        positions = [point] if first else stroke_points(last, point, max(1, stroke["size"] / 5))
        for position in positions:
            box = edits.stamp(position, *brush)
            if box:
                area = box if area is None else (
                    min(area[0], box[0]), min(area[1], box[1]), max(area[2], box[2]), max(area[3], box[3]))
        stroke["last"] = point
        if area:
            self.surface.patch(edits.image, area)

    def mouseReleaseEvent(self, event):
        if event.button() == MIDDLE:
            self.pan = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.editor.floating_clip is not None
                           else Qt.CursorShape.CrossCursor)
        if event.button() != LEFT:
            return
        editor = self.editor
        if self.paste_drag is not None:
            self.position_paste(event)
            self.paste_drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        point = self.clamp(self.pixel_position(event))
        if self.sketch is not None:
            rectangle = editor.tool.currentText() == "Rectangle select"
            points = [self.sketch[0], point] if rectangle else [*self.sketch, point]
            self.sketch = None
            if len(set(points)) >= (2 if rectangle else 3):
                editor.set_selection(points, rectangle)
            self.viewport().update()
        elif self.moving is not None:
            offset = self.move_offset(self.pixel_position(event))
            dragging, self.moving = self.moving, None
            if offset == (0, 0):
                editor.refresh_image()
            else:
                editor.finish_move(dragging, offset)
        elif self.stroke is not None:
            self.paint_stroke(point)
            detail = {k: v for k, v in self.stroke.items() if k not in ("last", "offset")}
            detail["end"] = point
            if self.stroke["offset"] is not None:
                detail["sample"] = editor.clone_anchor
            editor.edits.end_stroke(editor.with_selection(detail))
            self.stroke = None
            editor.refresh_controls()
        event.accept()

    def drawForeground(self, painter, rect):
        if self.editor.before.isChecked() or self.editor.cutout_preview.isChecked():
            return
        if self.editor.floating_clip is not None:
            bounds = self.editor.paste_rect()
            painter.save()
            painter.setClipRect(self.surface.boundingRect())
            painter.setWorldTransform(self.editor.paste_transform(), True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.save()
            painter.setOpacity(self.editor.paste_opacity.value() / 100)
            painter.translate(bounds.width() if self.editor.paste_flip_x.isChecked() else 0,
                              bounds.height() if self.editor.paste_flip_y.isChecked() else 0)
            painter.scale(-1 if self.editor.paste_flip_x.isChecked() else 1,
                          -1 if self.editor.paste_flip_y.isChecked() else 1)
            painter.drawImage(bounds, self.editor.floating_preview)
            painter.restore()
            painter.setPen(QPen(QColor("#56dbba"), 0, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(bounds)
            painter.restore()
            corners, top, rotate = self.paste_handles()
            radius = 3.5 / self.transform().m11()
            painter.setPen(QPen(QColor("#168274"), 0))
            painter.setBrush(QColor("#f3fffb"))
            painter.drawLine(top, rotate)
            for point in corners.values():
                painter.drawRect(QRectF(point.x() - radius, point.y() - radius, 2 * radius, 2 * radius))
            painter.drawEllipse(rotate, radius, radius)
            return
        if self.moving is not None:
            dragging = self.moving
            dx, dy = dragging["offset"]
            if (dx, dy) != (0, 0):
                left, top, _, _ = dragging["bounds"]
                painter.drawImage(QPointF(left + dx, top + dy), dragging["preview"])
        points = self.sketch if self.sketch is not None else self.editor.selection
        if self.moving is not None and points:
            dx, dy = self.moving["offset"]
            points = [(x + dx, y + dy) for x, y in points]
        if points:
            rectangle = (self.editor.tool.currentText() == "Rectangle select" if self.sketch is not None
                         else self.editor.selection_rectangle)
            for color, style in (("#ffffff", Qt.PenStyle.SolidLine), ("#233446", Qt.PenStyle.DashLine)):
                painter.setPen(QPen(QColor(color), 0, style))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                if rectangle:
                    a, b = (QPointF(x, y) for x, y in points)
                    painter.drawRect(QRectF(a, b).normalized())
                else:
                    painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        if self.editor.clone_anchor is not None and self.editor.tool.currentText() == "Clone stamp":
            point = QPointF(*self.editor.clone_anchor)
            size = 7 / max(0.01, self.transform().m11())
            painter.setPen(QPen(QColor("#30dabb"), 0))
            painter.drawLine(point - QPointF(size, 0), point + QPointF(size, 0))
            painter.drawLine(point - QPointF(0, size), point + QPointF(0, size))
        if self.hover is not None and self.stroke is None and self.editor.tool.currentText() in PAINT_TOOLS:
            radius = self.editor.size.value() / 2
            point = QPointF(*self.hover)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            outline = QPen(QColor("#ffffff"), 2)
            outline.setCosmetic(True)
            painter.setPen(outline)
            painter.drawEllipse(point, radius, radius)
            painter.setPen(QPen(QColor("#233446"), 0, Qt.PenStyle.DashLine))
            painter.drawEllipse(point, radius, radius)


class PhotoEditor(QDialog):
    def __init__(self, window, image, *, clipboard=None, source_asset=None):
        super().__init__(window)
        self.setObjectName("photoEditor")
        self.setWindowTitle("Edit Pixels")
        self.resize(1050, 720)
        self.edits = PhotoEdits(image)
        self.clipboard = PixelClipboard() if clipboard is None else clipboard
        self.source_asset = source_asset
        self.floating_clip = None
        self.floating_preview = None
        self.paste_position = (0, 0)
        self.paste_aspect = 1.0
        self.selection = None
        self.selection_base = None
        self.selection_rectangle = True
        self.selection_kind = ""
        self.mask = None
        self.clone_anchor = None
        self.layer_result = None

        outer = QVBoxLayout(self)
        header = QHBoxLayout()
        outer.addLayout(header)
        self.undo_button = self.button(header, "Undo", self.undo)
        self.redo_button = self.button(header, "Redo", self.redo)
        self.button(header, "Fit", lambda: self.view.fit())
        self.button(header, "100%", lambda: self.view.actual_pixels())
        self.before = QCheckBox("Show before")
        self.before.toggled.connect(lambda checked: self.toggle_preview(self.cutout_preview, checked))
        header.addWidget(self.before)
        self.cutout_preview = QCheckBox("Preview cutout")
        self.cutout_preview.toggled.connect(lambda checked: self.toggle_preview(self.before, checked))
        header.addWidget(self.cutout_preview)
        header.addStretch()
        self.zoom = QLabel()
        header.addWidget(self.zoom)

        self.placement_controls = QWidget()
        placement_stack = QVBoxLayout(self.placement_controls)
        placement_stack.setContentsMargins(0, 0, 0, 0)
        placement = QHBoxLayout()
        placement_stack.addLayout(placement)
        self.paste_width = self.placement_field(placement, "Width", QSpinBox(), 1, 20000, " px")
        self.paste_height = self.placement_field(placement, "Height", QSpinBox(), 1, 20000, " px")
        self.paste_rotation = self.placement_field(placement, "Rotation", QDoubleSpinBox(), -180, 180, "°")
        self.paste_rotation.setDecimals(1)
        self.paste_ratio = QCheckBox("Lock ratio")
        self.paste_ratio.setChecked(True)
        self.paste_ratio.toggled.connect(self.lock_paste_ratio)
        placement.addWidget(self.paste_ratio)
        self.paste_opacity = self.placement_field(placement, "Opacity", QSpinBox(), 0, 100, "%")
        self.paste_opacity.setValue(100)
        placement.addStretch()
        self.paste_width.valueChanged.connect(lambda _: self.resize_paste(self.paste_width))
        self.paste_height.valueChanged.connect(lambda _: self.resize_paste(self.paste_height))
        self.paste_rotation.valueChanged.connect(lambda _: self.set_paste_position(*self.paste_position))
        actions = QHBoxLayout()
        placement_stack.addLayout(actions)
        self.paste_flip_x = QCheckBox("Flip Horizontal")
        self.paste_flip_y = QCheckBox("Flip Vertical")
        actions.addWidget(self.paste_flip_x)
        actions.addWidget(self.paste_flip_y)
        self.paste_flip_x.toggled.connect(lambda _: self.view.viewport().update())
        self.paste_flip_y.toggled.connect(lambda _: self.view.viewport().update())
        self.paste_opacity.valueChanged.connect(lambda _: self.view.viewport().update())
        self.button(actions, "Reset Transform", self.reset_paste)
        self.button(actions, "Place Selection", self.place_paste)
        self.button(actions, "Cancel Paste", self.cancel_paste)
        actions.addStretch()
        self.placement_controls.hide()
        outer.addWidget(self.placement_controls)

        content = QHBoxLayout()
        outer.addLayout(content, 1)
        self.view = PhotoView(self)
        content.addWidget(self.view, 1)
        self.tabs = QTabWidget()
        self.tabs.setMaximumWidth(330)
        content.addWidget(self.tabs)
        self.tabs.addTab(self.retouch_controls(), "Retouch")
        self.tabs.addTab(self.selection_controls(), "Selection")
        self.tabs.addTab(self.adjustment_controls(), "Adjust")
        self.tabs.addTab(self.filter_controls(), "Filters")
        self.note = QLabel()
        self.note.setWordWrap(True)
        outer.addWidget(self.note)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = controls.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText("Apply to Layer")
        self.apply_button.setObjectName("applyPhoto")
        self.apply_button.setAutoDefault(False)
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        outer.addWidget(controls)
        for sequence, callback in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo),
                                   ("Ctrl+Shift+Z", self.redo), ("Ctrl+D", self.clear_selection),
                                   ("Ctrl+C", self.copy_pixels), ("Ctrl+V", self.start_paste)):
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(callback)
            self.addAction(action)
        self.tool_changed()
        self.refresh_controls()
        QTimer.singleShot(0, self.view.fit)

    @staticmethod
    def button(layout, title, callback):
        button = QPushButton(title)
        button.setAutoDefault(False)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    @staticmethod
    def placement_field(layout, title, field, minimum, maximum, suffix):
        label = QLabel(title)
        label.setBuddy(field)
        field.setRange(minimum, maximum)
        field.setSuffix(suffix)
        field.setKeyboardTracking(False)
        layout.addWidget(label)
        layout.addWidget(field)
        return field

    @staticmethod
    def spin(form, title, limits, default, decimal=False):
        field = QDoubleSpinBox() if decimal else QSpinBox()
        field.setRange(*limits)
        if decimal:
            field.setSingleStep(0.1)
        field.setValue(default)
        form.addRow(title, field)
        return field

    @staticmethod
    def panel():
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        layout.addLayout(form)
        return widget, layout, form

    def retouch_controls(self):
        widget, layout, form = self.panel()
        self.tool = QComboBox()
        self.tool.setObjectName("photoTool")
        self.tool.addItems((*PAINT_TOOLS, *SELECT_TOOLS, COLOR_TOOL, MOVE_TOOL))
        self.tool.currentTextChanged.connect(self.tool_changed)
        form.addRow("Tool", self.tool)
        self.size = self.spin(form, "Brush size (px)", (1, 256), 30)
        self.hardness = self.spin(form, "Hardness (%)", (0, 100), 70)
        self.opacity = self.spin(form, "Opacity (%)", (1, 100), 100)
        self.color_button = QPushButton()
        self.color_button.setAutoDefault(False)
        self.color_button.clicked.connect(self.choose_color)
        form.addRow("Brush color", self.color_button)
        self.set_color(QColor("#253246"))
        layout.addStretch()
        return widget

    def selection_controls(self):
        widget, layout, form = self.panel()
        self.selection_mode = QComboBox()
        self.selection_mode.addItems(("Replace", "Add", "Subtract"))
        form.addRow("Selection mode", self.selection_mode)
        self.tolerance = self.spin(form, "Color tolerance", (0, 255), 24)
        self.feather = self.spin(form, "Feather (px)", (0, 100), 0)
        self.feather.valueChanged.connect(self.update_mask)
        for title, tool in (("Rectangle", SELECT_TOOLS[0]), ("Freehand", SELECT_TOOLS[1]),
                            ("Select by Color", COLOR_TOOL), ("Move Selection", MOVE_TOOL)):
            self.button(layout, title, lambda _checked=False, name=tool: self.tool.setCurrentText(name))
        options = QHBoxLayout()
        self.button(options, "Invert", self.invert_selection)
        self.button(options, "Deselect", self.clear_selection)
        layout.addLayout(options)
        self.copy_pixels_button = self.button(layout, "Copy Pixels", self.copy_pixels)
        self.paste_pixels_button = self.button(layout, "Paste into Layer", self.start_paste)
        self.copy_layer_button = self.button(layout, "Copy to New Layer", lambda: self.send_to_layer(False))
        self.cut_layer_button = self.button(layout, "Cut to New Layer", lambda: self.send_to_layer(True))
        self.erase_selection_button = self.button(layout, "Erase Selected Pixels", self.erase_selection)
        layout.addStretch()
        return widget

    def adjustment_controls(self):
        widget, layout, form = self.panel()
        self.channel = QComboBox()
        self.channel.addItems(("RGB", "Red", "Green", "Blue"))
        form.addRow("Levels channel", self.channel)
        self.black = self.spin(form, "Black point", (0, 254), 0)
        self.white = self.spin(form, "White point", (1, 255), 255)
        self.gamma = self.spin(form, "Midtone gamma", (0.1, 4.0), 1.0, decimal=True)
        self.saturation = self.spin(form, "Saturation (%)", (0, 250), 100)
        self.button(layout, "Apply Levels & Saturation", self.adjust)
        for label, transform in (("Rotate 90° clockwise", Image.Transpose.ROTATE_270),
                                 ("Flip horizontally", Image.Transpose.FLIP_LEFT_RIGHT),
                                 ("Flip vertically", Image.Transpose.FLIP_TOP_BOTTOM)):
            self.button(layout, label, lambda checked=False, name=label, mode=transform: self.transform(name, mode))
        layout.addStretch()
        return widget

    def filter_controls(self):
        widget, layout, form = self.panel()
        self.filter_name = QComboBox()
        self.filter_name.addItems(("Gaussian blur", "Unsharp mask", "Invert colors"))
        form.addRow("Filter", self.filter_name)
        self.radius = self.spin(form, "Radius (px)", (0.1, 64.0), 2.0, decimal=True)
        self.strength = self.spin(form, "Sharpen amount (%)", (1, 300), 100)
        self.button(layout, "Apply Filter", self.apply_filter)
        layout.addStretch()
        return widget

    def set_color(self, color):
        self.color = color
        self.color_button.setText(color.name().upper())
        self.color_button.setStyleSheet(f"border-left: 14px solid {color.name()};")

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self, "Brush color")
        if color.isValid():
            self.set_color(color)

    def tool_changed(self):
        text = {
            "Clone stamp": "Alt-click or right-click to sample, then paint to copy those pixels.",
            "Eraser": "Paint to remove pixels and reveal lower layers.",
            "Brush": "Paint on the image. Alt-click samples a color.",
            "Rectangle select": "Drag a rectangle to limit painting and adjustments.",
            "Freehand select": "Trace around an area to limit painting and adjustments.",
            COLOR_TOOL: "Click a color to select matching pixels. Use Invert to keep everything else.",
            MOVE_TOOL: "Drag inside the selection to move those pixels and leave transparency behind.",
        }[self.tool.currentText()]
        if self.cutout_preview.isChecked():
            self.cutout_preview.setChecked(False)
        self.note.setText(text)
        self.view.viewport().update()

    def set_selection(self, points, rectangle):
        mask = selection_mask(self.edits.image.size, points, rectangle, feather=0)
        self.replace_selection(mask, "rectangle" if rectangle else "freehand", points)

    def select_color(self, point):
        mask = color_selection(self.edits.image, point, self.tolerance.value())
        self.replace_selection(mask, "matching color")

    def replace_selection(self, mask, kind, points=None):
        mode = self.selection_mode.currentText()
        if self.selection_base is not None and mode != "Replace":
            if mode == "Add":
                mask = ImageChops.lighter(self.selection_base, mask)
            else:
                mask = ImageChops.subtract(self.selection_base, mask)
            kind, points = "combined", None
        self.set_selection_mask(mask, kind, points)

    def set_selection_mask(self, mask, kind, points=None):
        bounds = mask.getbbox()
        self.selection_base = mask if bounds else None
        self.selection_kind = kind if bounds else ""
        self.selection_rectangle = kind != "freehand" or points is None
        self.selection = None
        if bounds:
            self.selection = ([(bounds[0], bounds[1]), (bounds[2], bounds[3])]
                              if self.selection_rectangle else points)
        self.update_mask()

    def update_mask(self):
        self.mask = self.selection_base
        if self.mask is not None and self.feather.value():
            self.mask = self.mask.filter(ImageFilter.GaussianBlur(self.feather.value()))
        if self.cutout_preview.isChecked():
            if self.mask is None:
                self.cutout_preview.setChecked(False)
            else:
                self.refresh_image()
        else:
            self.view.viewport().update()
        self.refresh_selection_controls()

    def clear_selection(self):
        self.selection = None
        self.selection_base = None
        self.selection_kind = ""
        self.update_mask()

    def invert_selection(self):
        mask = (Image.new("L", self.edits.image.size, 255) if self.selection_base is None
                else ImageOps.invert(self.selection_base))
        self.set_selection_mask(mask, "inverted")

    def with_selection(self, detail):
        if self.mask is not None:
            return {**detail, "selection": self.selection_kind,
                    "selection_bounds": self.mask.getbbox(), "feather_px": self.feather.value()}
        return detail

    def erase_selection(self):
        if self.mask is None:
            self.note.setText("Draw a selection first.")
            return
        self.edits.apply(erase_mask(self.edits.image, self.mask), self.with_selection({"tool": "Erase selection"}))
        self.show_after()

    def send_to_layer(self, cut):
        lifted = lift_selection(self.edits.image, self.mask) if self.mask is not None else None
        if lifted is None:
            self.note.setText("Select some visible pixels first.")
            return
        self.layer_result = (cut, *lifted)
        self.accept()

    def copy_pixels(self):
        if self.floating_clip is not None:
            return
        lifted = lift_selection(self.edits.image, self.mask) if self.mask is not None else None
        if lifted is None:
            self.note.setText("Select some visible pixels first.")
            return
        pixels, _ = lifted
        sources = pasted_sources(self.edits.operations)
        if self.source_asset is not None:
            sources = list(dict.fromkeys([self.source_asset, *sources]))
        self.clipboard.clip = PixelClip.capture(pixels, sources)
        self.refresh_selection_controls()
        self.note.setText("Pixels copied. Paste here, or open another image layer to paste there.")

    def start_paste(self):
        if self.floating_clip is not None:
            return
        if self.clipboard.clip is None:
            self.note.setText("Copy a pixel selection first.")
            return
        self.show_after()
        self.clear_selection()
        self.floating_clip = self.clipboard.clip
        self.floating_preview = qt_pixels(self.floating_clip.image)
        self.reset_paste()
        self.note.setText("Drag a corner to resize or the round handle to rotate. Hold Shift to snap rotation.")
        self.placement_controls.show()
        self.view.setCursor(Qt.CursorShape.OpenHandCursor)
        self.view.setFocus()
        self.refresh_controls()

    def paste_rect(self):
        return QRectF(0, 0, self.paste_width.value(), self.paste_height.value())

    def paste_transform(self, position=None):
        x, y = self.paste_position if position is None else position
        center = self.paste_rect().center()
        return (QTransform().translate(x + center.x(), y + center.y())
                .rotate(self.paste_rotation.value()).translate(-center.x(), -center.y()))

    def lock_paste_ratio(self, checked):
        if checked:
            self.paste_aspect = self.paste_width.value() / self.paste_height.value()

    def reset_paste(self):
        pixels = self.floating_clip.image
        target = self.edits.image
        with QSignalBlocker(self.paste_width), QSignalBlocker(self.paste_height), QSignalBlocker(self.paste_rotation):
            self.paste_width.setValue(pixels.width)
            self.paste_height.setValue(pixels.height)
            self.paste_rotation.setValue(0)
        self.paste_ratio.setChecked(True)
        self.lock_paste_ratio(True)
        self.paste_flip_x.setChecked(False)
        self.paste_flip_y.setChecked(False)
        self.paste_opacity.setValue(100)
        self.set_paste_position((target.width - pixels.width) // 2, (target.height - pixels.height) // 2)

    def resize_from_corner(self, drag, point):
        sx, sy = drag["sign"]
        anchor = drag["anchor"]
        delta = QTransform().rotate(-drag["angle"]).map(point - drag["offset"] - anchor)
        width, height = max(0, delta.x() * sx), max(0, delta.y() * sy)
        max_width, max_height = self.paste_width.maximum(), self.paste_height.maximum()
        if self.paste_ratio.isChecked():
            start_width, start_height = drag["size"]
            scale = (width * start_width + height * start_height) / (start_width**2 + start_height**2)
            scale = min(scale, max_width / start_width, max_height / start_height)
            width, height = start_width * scale, start_height * scale
        width = max(1, min(round(width), max_width))
        height = max(1, min(round(height), max_height))
        with QSignalBlocker(self.paste_width), QSignalBlocker(self.paste_height):
            self.paste_width.setValue(width)
            self.paste_height.setValue(height)
        center = anchor + QTransform().rotate(drag["angle"]).map(QPointF(sx * width / 2, sy * height / 2))
        self.set_paste_position(round(center.x() - width / 2), round(center.y() - height / 2))

    def resize_paste(self, edited):
        if self.paste_ratio.isChecked():
            if edited is self.paste_width:
                other, scale = self.paste_height, 1 / self.paste_aspect
            else:
                other, scale = self.paste_width, self.paste_aspect
            with QSignalBlocker(edited), QSignalBlocker(other):
                desired = round(edited.value() * scale)
                if desired > other.maximum():
                    edited.setValue(min(edited.maximum(), int(other.maximum() / scale)))
                other.setValue(round(edited.value() * scale))
        self.set_paste_position(*self.paste_position)

    def set_paste_position(self, x, y):
        bounds = self.paste_transform((0, 0)).mapRect(self.paste_rect()).toAlignedRect()
        target = self.edits.image
        self.paste_position = (
            max(-bounds.right(), min(x, target.width - 1 - bounds.left())),
            max(-bounds.bottom(), min(y, target.height - 1 - bounds.top())),
        )
        self.view.viewport().update()

    def place_paste(self):
        if self.floating_clip is None:
            return
        # Commit a numeric field if placement was triggered while typing.
        for field in (self.paste_width, self.paste_height, self.paste_rotation, self.paste_opacity):
            field.interpretText()
        clip = self.floating_clip
        size = self.paste_width.value(), self.paste_height.value()
        rotation = self.paste_rotation.value()
        opacity = self.paste_opacity.value()
        flip_x, flip_y = self.paste_flip_x.isChecked(), self.paste_flip_y.isChecked()
        composed = paste_pixels(self.edits.image, clip.image, self.paste_position, size, rotation,
                                opacity=opacity, flip_x=flip_x, flip_y=flip_y)
        self.edits.apply(composed, {"tool": PASTE_TOOL, "source_assets": list(clip.source_assets),
                                    "selection_hash": clip.sha256, "position_px": list(self.paste_position),
                                    "size_px": list(size), "rotation_deg": rotation, "opacity_pct": opacity,
                                    "flip_horizontal": flip_x, "flip_vertical": flip_y})
        self.end_paste()
        self.note.setText("Selection placed. Click Apply to Layer to save the edit.")

    def end_paste(self):
        self.floating_clip = None
        self.floating_preview = None
        self.view.paste_drag = None
        self.view.setCursor(Qt.CursorShape.CrossCursor)
        self.placement_controls.hide()
        self.refresh_image()

    def cancel_paste(self):
        if self.floating_clip is not None:
            self.end_paste()
            self.tool_changed()

    def reject(self):
        if self.floating_clip is not None:
            self.cancel_paste()
        else:
            super().reject()

    def finish_move(self, dragging, offset):
        dx, dy = offset
        left, top, _, _ = dragging["bounds"]
        result = move_pixels(dragging["remainder"], dragging["pixels"], (left + dx, top + dy))
        self.edits.apply(result, self.with_selection({"tool": MOVE_TOOL, "offset_px": [dx, dy]}))
        shifted = Image.new("L", self.edits.image.size)
        shifted.paste(self.selection_base, (dx, dy))
        self.set_selection_mask(shifted, "moved")
        self.show_after()

    def adjust(self):
        black, white = self.black.value(), self.white.value()
        if black >= white:
            self.note.setText("Black point must be lower than white point.")
            return
        gamma, saturation, channel = self.gamma.value(), self.saturation.value(), self.channel.currentText()
        result = levels(self.edits.image, black, white, gamma, saturation, channel)
        self.apply_edit(result, {"tool": "Levels and saturation", "black": black, "white": white,
                                 "gamma": gamma, "saturation": saturation, "channel": channel})

    def apply_filter(self):
        name, radius, strength = self.filter_name.currentText(), self.radius.value(), self.strength.value()
        detail = {"tool": name}
        if name != "Invert colors":
            detail["radius_px"] = radius
        if name == "Unsharp mask":
            detail["strength_pct"] = strength
        self.apply_edit(filtered(self.edits.image, name, radius, strength), detail)

    def transform(self, name, mode):
        self.clear_selection()
        self.clone_anchor = None
        self.apply_edit(self.edits.image.transpose(mode), {"tool": name})
        self.view.fit()

    def apply_edit(self, image, detail):
        self.edits.apply(image, self.with_selection(detail), self.mask)
        self.show_after()

    def undo(self):
        if self.floating_clip is not None:
            self.cancel_paste()
            return
        if self.edits.undo_stack and self.edits.undo_stack[-1][1]["tool"] == MOVE_TOOL:
            self.clear_selection()
        self.edits.undo()
        self.show_after()

    def redo(self):
        if self.floating_clip is not None:
            return
        if self.edits.redo_stack and self.edits.redo_stack[-1][1]["tool"] == MOVE_TOOL:
            self.clear_selection()
        self.edits.redo()
        self.show_after()

    def show_after(self):
        if self.before.isChecked():
            self.before.setChecked(False)
        elif self.cutout_preview.isChecked():
            self.cutout_preview.setChecked(False)
        else:
            self.refresh_image()

    def toggle_preview(self, other, checked):
        if checked and other.isChecked():
            other.setChecked(False)
        self.refresh_image()

    def refresh_image(self):
        image = self.edits.original if self.before.isChecked() else self.edits.image
        if self.cutout_preview.isChecked() and self.mask is not None:
            image = masked_pixels(image, self.mask)
        size_changed = self.view.surface.boundingRect().size().toTuple() != image.size
        self.view.surface.replace(image)
        if size_changed:
            if not self.before.isChecked() and self.mask is not None and self.mask.size != image.size:
                self.clear_selection()
            self.view.reframe()
            self.view.fit()
        self.refresh_controls()

    def refresh_controls(self):
        placing = self.floating_clip is not None
        self.tabs.setEnabled(not placing)
        self.before.setEnabled(not placing)
        self.undo_button.setEnabled(not placing and bool(self.edits.undo_stack))
        self.redo_button.setEnabled(not placing and bool(self.edits.redo_stack))
        self.apply_button.setEnabled(not placing and bool(self.edits.operations))
        self.refresh_selection_controls()
        self.update_zoom()

    def refresh_selection_controls(self):
        selected = self.mask is not None
        self.paste_pixels_button.setEnabled(self.clipboard.clip is not None)
        for button in (self.copy_pixels_button, self.copy_layer_button, self.cut_layer_button,
                       self.erase_selection_button, self.cutout_preview):
            button.setEnabled(selected)

    def update_zoom(self):
        image = self.view.surface.image
        zoom = self.view.transform().m11()
        self.zoom.setText(f"{image.width()} × {image.height()} px   ·   {zoom:.0%}")
