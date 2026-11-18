"""Qt graphics-view artboard, drawing gestures and exact layer manipulation."""
from __future__ import annotations

import math
from copy import deepcopy

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView, QWidget

from .briefs import scaffold_lines
from .project import descendants, front_layers, is_locked
from .render import crop_rect, decode_png, draw_local, local_points, notice_object, page_notice, resolved, rgba

LEFT = Qt.MouseButton.LeftButton
MID = Qt.MouseButton.MiddleButton
SHIFT = Qt.KeyboardModifier.ShiftModifier
CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier
AQUA = "#147d73"


class Artwork(QGraphicsItem):
    def __init__(self, key, raw, alpha, canvas, parent):
        super().__init__(parent)
        self.key, self.canvas, self.obj = key, canvas, None
        self.setOpacity(alpha)
        self.set_shape(raw)

    def set_shape(self, raw):
        self.prepareGeometryChange()
        self.obj = raw
        self.setPos(raw["x"],raw["y"])
        self.setRotation(raw["angle"])

    def boundingRect(self):
        bleed = max(1., self.obj["border"]/2)
        extra = max(bleed, 60 if self.obj["kind"] in ("arrow","connector") else bleed)
        return QRectF(-bleed,-bleed,self.obj["w"]+bleed+extra,self.obj["h"]+2*bleed+3)

    def paint(self,painter, option, widget=None):
        draw_local(painter,self.obj,self.canvas.window.project.state,self.canvas.window.project.sources)

    def frame(self):
        return QRectF(0, 0, self.obj["w"], self.obj["h"])

    def contains_pointer(self, local, tolerance, *, pixels=True):
        kind = self.obj["kind"]
        if kind not in ("path", "arrow", "connector", "polygon", "circle"):
            frame = self.frame()
            if not frame.contains(local):
                return False
            if kind != "image" or not pixels or rgba(self.obj["fill"]).alpha():
                return True
            border = self.obj["border"] / 2
            if border and rgba(self.obj["stroke"]).alpha():
                if not frame.adjusted(border, border, -border, -border).contains(local):
                    return True
            key = self.obj["asset"]
            image = decode_png(self.canvas.window.project.sources[key], key)
            cropped = crop_rect(self.obj, image)
            x = int(cropped.x() + cropped.width() * local.x() / frame.width())
            y = int(cropped.y() + cropped.height() * local.y() / frame.height())
            return image.valid(x, y) and image.pixelColor(x, y).alpha() > 0
        outline = QPainterPath()
        if kind == "circle":
            outline.addEllipse(self.frame())
        else:
            points = local_points(self.obj)
            if not points:
                return False
            outline.moveTo(*points[0])
            for x, y in points[1:]:
                outline.lineTo(x, y)
            if kind == "polygon":
                outline.closeSubpath()
        if kind in ("polygon", "circle") and outline.contains(local):
            return True
        stroker = QPainterPathStroker()
        stroker.setWidth(max(2 * tolerance, self.obj["border"]))
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        return stroker.createStroke(outline).contains(local)


class Ruler(QWidget):
    def __init__(self, canvas, horizontal):
        super().__init__(canvas)
        self.canvas, self.horizontal = canvas, horizontal
        self.setToolTip("Click the ruler to add a guide. Clear guides from the View menu.")

    def mousePressEvent(self,event):
        at = event.position().toPoint()
        pos = self.canvas.mapToScene(QPoint(at.x() if self.horizontal else 0,
                                           0 if self.horizontal else at.y()))
        self.canvas.window.add_guide("x" if self.horizontal else "y",pos.x() if self.horizontal else pos.y())

    def paintEvent(self,event):
        c = self.canvas
        with QPainter(self) as p:
            p.fillRect(self.rect(),QColor("#24374a"))
            p.setPen(QColor("#a6bbc7"))
            face = QFont("Segoe UI"); face.setPixelSize(9); p.setFont(face)
            hor = self.horizontal
            limit = self.width() if hor else self.height()
            start = c.mapToScene(QPoint()).x() if hor else c.mapToScene(QPoint()).y()
            step = 5 if c.transform().m11() > 3 else 10 if c.transform().m11() > 1.5 else 25
            for tick in range(math.floor(start/step)*step,math.ceil((start+limit/max(.05,c.transform().m11()))/step)*step+step,step):
                pt = c.mapFromScene(QPointF(tick,0) if hor else QPointF(0,tick))
                at = pt.x() if hor else pt.y()
                labeled = tick % (step*2) == 0
                if hor:
                    p.drawLine(at,18,at,11 if labeled else 15)
                    if labeled: p.drawText(at+2,9,str(tick))
                else:
                    p.drawLine(27,at,21 if labeled else 24,at)
                    if labeled: p.drawText(1,at+3,str(tick))


class Canvas(QGraphicsView):
    def __init__(self,window):
        super().__init__()
        self.window = window
        self.setObjectName("canvas")
        self.setAccessibleName("Editable vector canvas")
        self.setScene(QGraphicsScene(self))
        self.setViewportMargins(28,19,0,0)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing |
                            QPainter.RenderHint.SmoothPixmapTransform)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#203143"))
        tile = QPixmap(16, 16)
        tile.fill(QColor("#d7dde1"))
        with QPainter(tile) as painter:
            painter.fillRect(0, 0, 8, 8, QColor("#f2f4f5"))
            painter.fillRect(8, 8, 8, 8, QColor("#f2f4f5"))
        self.checker = QBrush(tile)
        self.hr,self.vr = Ruler(self,True),Ruler(self,False)
        self.horizontalScrollBar().valueChanged.connect(self.ruler_refresh)
        self.verticalScrollBar().valueChanged.connect(self.ruler_refresh)
        self.items_by_id = {}
        self.grid, self.snap, self.guides = True, True, True
        self.space = False
        self.gesture = None
        self.overlay = None
        self.active_tool = "select"

    def set_tool(self, tool):
        if self.gesture is not None:
            self.rebuild()
        self.active_tool = tool
        self.update_cursor()
        self.viewport().update()

    def update_cursor(self):
        if self.gesture and self.gesture["mode"] in ("pan", "move"):
            cursor = Qt.CursorShape.ClosedHandCursor
        elif self.space:
            cursor = Qt.CursorShape.OpenHandCursor
        elif self.active_tool != "select":
            cursor = Qt.CursorShape.CrossCursor
        else:
            cursor = Qt.CursorShape.ArrowCursor
        self.viewport().setCursor(cursor)

    def size_mm(self):
        p = self.window.project.state["paper"]
        return p["w"],p["h"]

    def rebuild(self, fit=False):
        self.gesture = None
        self.overlay = None
        scene = self.scene()
        scene.clear()
        self.items_by_id.clear()
        state = self.window.project.state
        w,h = self.size_mm()
        scene.setSceneRect(-60,-60,w+120,h+120)
        shadow = scene.addRect(2,3,w,h,QPen(Qt.PenStyle.NoPen),QColor("#132233"))
        background = rgba(state["paper"]["background"])
        paper = scene.addRect(0, 0, w, h, QPen(Qt.PenStyle.NoPen),
                              QBrush(background) if background.alpha() else self.checker)
        paper.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
        for z,(key,raw,alpha) in enumerate(front_layers(state),1):
            item = Artwork(key,resolved(raw,state),alpha,self,paper)
            item.setZValue(z)
            self.items_by_id[key] = item
        if page_notice(state):
            item = Artwork(None,notice_object(state),1,self,paper)
            item.setZValue(100000)
        if fit:
            self.fit()
        self.update_cursor()
        self.viewport().update()
        self.ruler_refresh()

    def fit(self):
        w,h = self.size_mm()
        self.resetTransform()
        self.fitInView(QRectF(-14,-14,w+28,h+28),Qt.AspectRatioMode.KeepAspectRatio)
        self.centerOn(w/2,h/2)
        self.ruler_refresh()
        self.window.zoom_status()

    def zoom(self,factor):
        current = self.transform().m11()
        if .12 <= current * factor <= 30:
            self.scale(factor,factor)
        self.ruler_refresh()
        self.viewport().update()
        self.window.zoom_status()

    def ruler_refresh(self,*_):
        self.hr.update(); self.vr.update()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        view = self.viewport().geometry()
        self.hr.setGeometry(view.left(),view.top()-19,view.width(),19)
        self.vr.setGeometry(view.left()-28,view.top(),28,view.height())
        self.hr.raise_(); self.vr.raise_()

    def wheelEvent(self,event):
        if event.modifiers() & CTRL:
            self.zoom(1.16 ** (event.angleDelta().y()/120))
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self,event):
        k = event.key()
        if k == Qt.Key.Key_Space:
            self.space = True
            self.update_cursor()
            event.accept()
        elif k in (Qt.Key.Key_Left,Qt.Key.Key_Right,Qt.Key.Key_Up,Qt.Key.Key_Down):
            step = 5 if event.modifiers() & SHIFT else 1
            self.window.nudge(step*((k==Qt.Key.Key_Right)-(k==Qt.Key.Key_Left)),
                              step*((k==Qt.Key.Key_Down)-(k==Qt.Key.Key_Up)))
            event.accept()
        elif k == Qt.Key.Key_Escape:
            self.window.select_tool(0)
            self.window.set_selection([])
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self,event):
        if event.key() == Qt.Key.Key_Space:
            self.space = False
            self.update_cursor()
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self.space = False
        self.update_cursor()
        super().focusOutEvent(event)

    def guide_positions(self):
        return self.window.project.state["guides"] + [list(g) for g in scaffold_lines(self.window.project.state)[0]]

    def snapped(self,pos,event):
        if not self.snap or event.modifiers() & ALT:
            return pos
        x,y = pos.x(),pos.y()
        grid = 5
        positions = self.guide_positions() if self.guides else []
        tol = 7/max(.1,self.transform().m11())
        nearx = [round(x/grid)*grid] if self.grid else []
        neary = [round(y/grid)*grid] if self.grid else []
        nearx.extend(v for key,v in positions if key=="x")
        neary.extend(v for key,v in positions if key=="y")
        targetx = min(nearx,key=lambda a:abs(a-x),default=x)
        targety = min(neary,key=lambda a:abs(a-y),default=y)
        return QPointF(targetx if abs(targetx-x)<=tol else x,targety if abs(targety-y)<=tol else y)

    def canvas_selection(self):
        state = self.window.project.state
        return [key for key in descendants(state,self.window.selected,leaf=True) if key in self.items_by_id]

    def handles(self,key):
        item = self.items_by_id[key]
        w,h = item.obj["w"],item.obj["h"]
        r = 18/max(self.transform().m11(),.08)
        return {name:item.mapToScene(QPointF(x,y)) for name,x,y in [
            ("nw",0,0),("ne",w,0),("sw",0,h),("se",w,h),("rotate",w/2,-r)]}

    def drawForeground(self,painter,rect):
        state = self.window.project.state
        w,h = self.size_mm()
        scale = max(.08,self.transform().m11())
        if self.grid and scale > 1.25:
            p = QPen(QColor(50,112,125,40),0)
            painter.setPen(p)
            for x in range(5,math.ceil(w),5):
                painter.drawLine(QPointF(x,0),QPointF(x,h))
            for y in range(5,math.ceil(h),5):
                painter.drawLine(QPointF(0,y),QPointF(w,y))
        if self.guides:
            painter.setPen(QPen(QColor("#34a599"),0,Qt.PenStyle.DashLine))
            for axis,at in self.guide_positions():
                painter.drawLine(QPointF(at,0),QPointF(at,h)) if axis=="x" else painter.drawLine(QPointF(0,at),QPointF(w,at))
        painter.setPen(QPen(QColor(AQUA),0,Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        keys = self.canvas_selection()
        for key in keys:
            item = self.items_by_id[key]
            painter.drawPolygon(item.mapToScene(item.frame()))
        if (self.active_tool == "select" and len(self.window.selected) == 1
                and self.window.selected[0] in keys and keys[0] in self.window.movable_layers()):
            points = self.handles(keys[0])
            painter.setPen(QPen(QColor(AQUA),0))
            painter.setBrush(QColor("#f4f9f9"))
            d = 3.5/scale
            pos = points["rotate"]
            painter.drawLine((points["ne"]+points["nw"])/2,pos)
            for name,pt in points.items():
                r = QRectF(pt.x()-d,pt.y()-d,2*d,2*d)
                (painter.drawEllipse if name=="rotate" else painter.drawRect)(r)
        if self.overlay:
            painter.setPen(QPen(QColor(AQUA),0,Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            mode,points = self.overlay
            if mode == "region":
                painter.setBrush(QColor(42, 160, 147, 50))
                painter.drawPolygon(points)
            elif mode in ("path","polygon","arrow","connector"):
                for a,b in zip(points,points[1:]): painter.drawLine(a,b)
            else:
                painter.drawRect(QRectF(points[0],points[-1]).normalized())

    def hit(self, point, *, unlocked=False, kind=None):
        position = self.mapToScene(point)
        w, h = self.size_mm()
        if not QRectF(0, 0, w, h).contains(position):
            return None
        tolerance = 6 / max(.08, self.transform().m11())
        state = self.window.project.state
        nearby = QRect(point.x() - 6, point.y() - 6, 13, 13)
        for item in self.items(nearby):
            if not isinstance(item, Artwork) or not item.key:
                continue
            if kind is not None and item.obj["kind"] != kind:
                continue
            if unlocked and is_locked(state, item.key):
                continue
            if item.contains_pointer(item.mapFromScene(position), tolerance, pixels=kind != "image"):
                return item.key
        return None

    def image_region(self, gesture, position):
        item = self.items_by_id[gesture["key"]]
        local = item.mapFromScene(position)
        frame = item.frame()
        region = QRectF(gesture["start"], local).normalized().intersected(frame)
        if region.width() >= 1 and region.height() >= 1:
            if region.left() < 1:
                region.setLeft(0)
            if region.top() < 1:
                region.setTop(0)
            if frame.right() - region.right() < 1:
                region.setRight(frame.right())
            if frame.bottom() - region.bottom() < 1:
                region.setBottom(frame.bottom())
        return region

    def mousePressEvent(self,event):
        point = event.position().toPoint()
        pos = self.mapToScene(point)
        if event.button() == MID or event.button() == LEFT and self.space:
            self.gesture = {"mode":"pan","point":point,"scroll":(self.horizontalScrollBar().value(),self.verticalScrollBar().value())}
            self.update_cursor()
            event.accept()
            return
        if event.button()!=LEFT:
            return super().mousePressEvent(event)
        self.setFocus()
        tool = self.active_tool
        state = self.window.project.state
        if tool == "region":
            key = self.hit(point, unlocked=True, kind="image")
            if key is None:
                self.window.statusBar().showMessage("Start inside an unlocked image on the canvas.", 4500)
                event.accept()
                return
            self.window.set_selection([key])
            self.gesture = {"mode": "region", "key": key, "start": self.items_by_id[key].mapFromScene(pos)}
            event.accept()
            return
        if tool != "select":
            w,h = self.size_mm()
            if not QRectF(0,0,w,h).contains(pos):
                return
            self.gesture = {"mode":"draw","tool":tool,"points":[self.snapped(pos,event)],"anchor":self.hit(point)}
            event.accept(); return
        if (len(self.window.selected) == 1 and (key := self.window.selected[0]) in self.items_by_id
                and key in self.window.movable_layers()):
            item = self.items_by_id[key]
            frame = item.frame()
            scale = self.transform().m11()
            corner_radius = max(1, min(7, min(frame.width(), frame.height()) * scale / 4))
            for name, pt in self.handles(key).items():
                radius = 8 if name == "rotate" else corner_radius
                if (point - self.mapFromScene(pt)).manhattanLength() <= radius:
                    initial = deepcopy(state["objects"][key])
                    self.gesture = {"mode": name, "start": pos, "point": point, "key": key,
                                    "original": initial, "handle": pt, "changes": {}}
                    event.accept(); return
        key = self.hit(point, unlocked=True)
        current = self.window.selected[:]
        if key is not None:
            within = key in self.canvas_selection()
            if event.modifiers() & (SHIFT|CTRL):
                self.window.set_selection([k for k in current if k!=key] if key in current else current+[key])
            elif not within:
                self.window.set_selection([key])
            ids = self.window.movable_layers()
            self.gesture = {"mode":"move","start":pos,"point":point,"changes":{},
                            "original":{k:deepcopy(state["objects"][k]) for k in ids}}
            if not ids and state["objects"][key]["kind"] == "connector":
                self.window.statusBar().showMessage("Move a linked object to reposition this connector.", 4000)
        else:
            self.gesture = {"mode":"marquee","start":pos,"point":point,
                            "prior":current if event.modifiers() & (SHIFT|CTRL) else []}
            if not event.modifiers() & (SHIFT|CTRL):
                self.window.set_selection([])
            if self.hit(point) is not None:
                self.window.statusBar().showMessage("This layer is locked. Unlock it in Layers to move it.", 4000)
        event.accept()

    def mouseMoveEvent(self,event):
        g = self.gesture
        if g is None:
            return super().mouseMoveEvent(event)
        point = event.position().toPoint()
        pos = self.mapToScene(point)
        mode = g["mode"]
        if mode=="pan":
            x,y = g["scroll"]
            d = point-g["point"]
            self.horizontalScrollBar().setValue(x-d.x())
            self.verticalScrollBar().setValue(y-d.y())
        elif mode == "region":
            region = self.image_region(g, pos)
            self.overlay = ("region", self.items_by_id[g["key"]].mapToScene(region))
        elif mode=="draw":
            tool = g["tool"]
            xy = self.snapped(pos,event)
            ps = g["points"]
            if tool in ("path","polygon") and len(ps)<4095:
                if (pos-ps[-1]).manhattanLength() >= 1.0:
                    ps.append(pos)
            self.overlay = (tool,ps+[xy])
        elif mode=="marquee":
            self.overlay = ("marquee",[g["start"],pos])
        elif mode != "move" or g["original"]:
            g["changes"] = self.transform_changes(g, pos, event)
            if mode == "move" and (point - g["point"]).manhattanLength() >= 3:
                self.update_cursor()
            self.ghost(g["changes"])
        self.viewport().update()
        event.accept()

    def transform_changes(self, g, pos, event):
        mode = g["mode"]
        if mode == "move":
            if not g["original"]:
                return {}
            first = next(iter(g["original"].values()))
            origin = QPointF(first["x"], first["y"])
            delta = self.snapped(origin + pos - g["start"], event) - origin
            return {key: {"x": round(obj["x"] + delta.x(), 4), "y": round(obj["y"] + delta.y(), 4)}
                    for key, obj in g["original"].items()}
        else:
            o = g["original"]
            offset = QPointF(o["x"],o["y"])
            if mode=="rotate":
                # Rotate around the object's top-left coordinate, keeping exact inspector origin fixed.
                a = g["start"]-offset; b = pos-offset
                angle = o["angle"] + math.degrees(math.atan2(b.y(),b.x())-math.atan2(a.y(),a.x()))
                angle = (angle+180)%360-180
                if event.modifiers() & SHIFT: angle = round(angle/15)*15
                props = {"angle":round(angle,3)}
            else:
                turn = QTransform().rotate(o["angle"])
                inverse,_ = turn.inverted()
                handle = g["handle"] + pos - g["start"]
                local = inverse.map(self.snapped(handle,event)-offset)
                left = min(local.x(),o["w"]-1) if "w" in mode else 0
                top = min(local.y(),o["h"]-1) if "n" in mode else 0
                right = max(local.x(),1) if "e" in mode else o["w"]
                bottom = max(local.y(),1) if "s" in mode else o["h"]
                new_pos = offset+turn.map(QPointF(left,top))
                props = {"x":round(new_pos.x(),4),"y":round(new_pos.y(),4),
                         "w":round(right-left,4),"h":round(bottom-top,4)}
            return {g["key"]: props}

    def ghost(self,changes):
        state = self.window.project.state
        updated = dict(state,objects={k:dict(o,**changes.get(k,{})) for k,o in state["objects"].items()})
        for key,item in self.items_by_id.items():
            raw = updated["objects"][key]
            if key in changes or raw["kind"]=="connector":
                item.set_shape(resolved(raw,updated))

    def mouseReleaseEvent(self,event):
        g = self.gesture
        if g is None:
            return super().mouseReleaseEvent(event)
        if event.button() not in (LEFT,MID):
            return
        self.gesture = None
        self.overlay = None
        self.update_cursor()
        pos = self.mapToScene(event.position().toPoint())
        mode = g["mode"]
        if mode == "region":
            region = self.image_region(g, pos)
            self.overlay = ("region", self.items_by_id[g["key"]].mapToScene(region))
            self.viewport().update()
            self.window.extract_image_region(g["key"], region)
            self.overlay = None
        elif mode=="draw":
            ps = g["points"] + [self.snapped(pos,event)]
            if (ps[-1]-ps[0]).manhattanLength()<1 and g["tool"] not in ("path","polygon"):
                ps[-1] = ps[0]+QPointF(55,23)
            self.window.draw_layer(g["tool"],ps,g["anchor"],self.hit(event.position().toPoint()))
        elif mode=="marquee":
            area = QRectF(g["start"],pos).normalized()
            state = self.window.project.state
            keys = g["prior"] + [k for k, item in self.items_by_id.items()
                                 if not is_locked(state, k) and area.intersects(item.mapRectToScene(item.frame()))]
            self.window.set_selection(list(dict.fromkeys(keys)))
        elif mode != "pan":
            if (event.position().toPoint() - g["point"]).manhattanLength() >= 3:
                changes = self.transform_changes(g, pos, event)
                action = {"move": "Drag selection", "rotate": "Rotate layer"}.get(mode, "Resize layer")
                if changes:
                    self.window.apply_geometry(action, changes)
            elif g["changes"]:
                self.rebuild()
        self.viewport().update()
        event.accept()

    def mouseDoubleClickEvent(self,event):
        if event.button()==LEFT and self.active_tool=="select":
            key = self.hit(event.position().toPoint(), unlocked=True)
            if key:
                self.window.edit_content(key)
                event.accept(); return
        super().mouseDoubleClickEvent(event)

