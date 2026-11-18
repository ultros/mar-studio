"""Shared canvas/print primitives. Reference images are separate from renderable layers."""
from __future__ import annotations

import base64
import math
import re
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QMarginsF, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QImageReader,
                           QPageLayout, QPageSize, QPainter, QPainterPath, QPdfWriter, QPen,
                           QPolygonF, QTransform)

from .integrity import Invalid, MAX_PIXELS, SCENARIO_NOTE, png_dimensions, require
from .project import front_layers

FONTS = Path(__file__).with_name("fonts")
FACE = "DejaVu Sans"
FONT_FILES = ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf")
TEXT_SCALE = 10
IMAGES = OrderedDict()


def register_fonts():
    for name in FONT_FILES:
        require(QFontDatabase.addApplicationFont(str(FONTS / name)) >= 0, f"Cannot load {name}")
    QImageReader.setAllocationLimit(300)


def rgba(value):
    return QColor(value[:7] if value.endswith("ff") else ("#" + value[-2:] + value[1:7]))


def qhex(color):
    return color.name(QColor.NameFormat.HexRgb) + f"{color.alpha():02x}"


@lru_cache(maxsize=512)
def font(size, bold=False, italic=False):
    result = QFont(FACE)
    result.setPixelSize(max(1, round(size * 25.4 / 72 * TEXT_SCALE)))
    result.setBold(bold)
    result.setItalic(italic)
    result.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
    return result


def decode_png(data, cache_key=None):
    if cache_key in IMAGES:
        IMAGES.move_to_end(cache_key)
        return IMAGES[cache_key]
    w, h = png_dimensions(data)
    device = QBuffer()
    device.setData(data)
    device.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(device, b"png")
    require(reader.size().width() == w and reader.size().height() == h, "PNG header and decoder disagree")
    source = reader.read()
    require(not source.isNull() and (source.width(), source.height()) == (w, h),
            "PNG could not be decoded safely")
    del reader
    device.close()
    if cache_key:
        while IMAGES and (sum(i.sizeInBytes() for i in IMAGES.values()) + source.sizeInBytes() > 150_000_000
                          or len(IMAGES) >= 16):
            IMAGES.popitem(last=False)
        if source.sizeInBytes() <= 150_000_000:
            IMAGES[cache_key] = source
    return source


def fresh_png(image, dpi=96):
    # Copy pixels to a new image; QImage otherwise carries source text/ICC/density into PNG export.
    result = QImage(image.size(), QImage.Format.Format_ARGB32)
    require(not result.isNull(), "Insufficient memory for raster encoding")
    result.fill(Qt.GlobalColor.transparent)
    result.setDotsPerMeterX(round(dpi / .0254))
    result.setDotsPerMeterY(round(dpi / .0254))
    with QPainter(result) as painter:
        painter.drawImage(QPointF(), image)
    return png_bytes(result)


def png_bytes(image):
    out = QBuffer()
    out.open(QIODevice.OpenModeFlag.WriteOnly)
    require(image.save(out, "PNG"), "Could not encode PNG")
    return bytes(out.data())


def raster(size, dpi=96):
    w, h = size
    require(1 <= w <= 20000 and 1 <= h <= 20000 and w*h <= MAX_PIXELS,
            f"{w} × {h} exceeds the {MAX_PIXELS:,}-pixel raster limit. Choose a smaller page or lower DPI.")
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    require(not img.isNull(), "Insufficient memory for raster export")
    img.setDotsPerMeterX(round(dpi / .0254))
    img.setDotsPerMeterY(round(dpi / .0254))
    img.fill(Qt.GlobalColor.transparent)
    return img


def text_lines(obj):
    """Line wrapping and inline bold/italic measured with the bundled font, in mm."""
    txt = obj["text"]
    if not txt:
        return []
    styles = [(False, False)] * len(txt)
    for a, b, bold, italic in obj["spans"]:
        styles[a:b] = [(bold, italic)] * (b - a)
    pad, size = obj["padding"], obj["size"]
    available = max(1, obj["w"] - 2*pad)
    base = QFontMetricsF(font(size))
    y = pad + base.ascent() / TEXT_SCALE
    step = base.height() / TEXT_SCALE * obj["line_height"]
    lines = []
    x, run, line_start = pad, [], True

    def flush():
        nonlocal y, x, run, line_start
        if y + base.descent() / TEXT_SCALE <= obj["h"] - pad + .01:
            lines.append((y, run))
        y += step
        x, run, line_start = pad, [], True

    def parts(a, b):
        out = []
        while a < b:
            end = a + 1
            while end < b and styles[end] == styles[a]:
                end += 1
            s = txt[a:end].replace("\t", "    ")
            bold, italic = styles[a]
            width = QFontMetricsF(font(size, bold, italic)).horizontalAdvance(s) / TEXT_SCALE
            out.append((s, width, bold, italic))
            a = end
        return out

    for match in re.finditer(r"\r\n|\r|\n|[^\S\r\n]+|[^\s]+", txt):
        token = match.group()
        if token in ("\r\n", "\n", "\r"):
            flush()
            continue
        if y > obj["h"]:
            break
        bits = parts(*match.span())
        width = sum(v[1] for v in bits)
        if not line_start and not token.isspace() and x + width > pad + available:
            flush()
        if token.isspace() and (line_start or x + width > pad + available):
            continue
        if width > available:
            bits = [bit for at in range(*match.span()) for bit in parts(at, at + 1)]
        for s, w, b, i in bits:
            if not line_start and x + w > pad + available and not token.isspace():
                flush()
            if run and run[-1][2:] == (b, i):
                last_x, last_s, _, _ = run[-1]
                run[-1] = (last_x, last_s + s, b, i)
            else:
                run.append((x, s, b, i))
            x += w
            line_start = False
    flush()
    return lines


def crop_rect(obj, image):
    return QRectF(*(v * (image.width() if i % 2 == 0 else image.height())
                    for i, v in enumerate(obj["crop"])))


def center(obj):
    transform = QTransform().translate(obj["x"], obj["y"]).rotate(obj["angle"])
    return transform.map(QPointF(obj["w"] / 2, obj["h"] / 2))


def attach(obj, toward):
    transform = QTransform().translate(obj["x"], obj["y"]).rotate(obj["angle"])
    inverse, _ = transform.inverted()
    c = QPointF(obj["w"] / 2, obj["h"] / 2)
    delta = inverse.map(toward) - c
    factor = max(abs(delta.x()) / c.x(), abs(delta.y()) / c.y())
    return transform.map(c + delta / factor) if factor > 0 else transform.map(c)


def resolved(obj, state):
    if obj["kind"] != "connector" or not (obj["start"] or obj["end"]):
        return obj
    others = state["objects"]
    start, end = others.get(obj["start"]), others.get(obj["end"])
    x, y, w, h = (obj[k] for k in ("x", "y", "w", "h"))
    tr = QTransform().translate(x,y).rotate(obj["angle"])
    a = center(start) if start else tr.map(QPointF(0, 0))
    b = center(end) if end else tr.map(QPointF(w, h))
    a2, b2 = attach(start, b) if start else a, attach(end, a) if end else b
    w2, h2 = max(1, abs(b2.x()-a2.x())), max(1, abs(b2.y()-a2.y()))
    nx, ny = min(a2.x(), b2.x()), min(a2.y(), b2.y())
    return dict(obj, x=nx, y=ny, w=w2, h=h2, angle=0, points=[
        [(p.x() - nx) / w2, (p.y() - ny) / h2] for p in (a2, b2)])


def local_points(obj):
    return [(p[0]*obj["w"], p[1]*obj["h"]) for p in obj["points"]]


def line_style(obj, scenario):
    return {"Shipment": [3, 1.8], "Money transfer": [1.8, 1.5, .4, 1.5],
            "Speculation": [.5, 1.5], "Proposed link": [2, 1.8]}.get(obj["edge"], []) if scenario else []


def primitives(obj, state):
    """Generate render instructions shared by painter and editable SVG."""
    w, h, t = obj["w"], obj["h"], obj["kind"]
    fill, stroke, border = obj["fill"], obj["stroke"], obj["border"]
    pad = obj["padding"]
    scenario = state["topic"] == "hypothesis"

    def rect(x, y, rw, rh, f=fill, s=stroke, b=border, r=0):
        return ("rect", x, y, rw, rh, r, f, s, b)

    if t in ("text", "panel", "label", "callout", "chart", "grid", "placeholder", "image"):
        yield rect(0, 0, w, h, r=min(w / 2, h / 2, obj["radius"]))
    if t == "circle":
        yield ("ellipse", 0, 0, w, h, fill, stroke, border)
    if t in ("polygon", "path", "arrow", "connector"):
        points = local_points(obj)
        if len(points) >= 2:
            edge = t in ("arrow", "connector")
            yield ("poly", points, t == "polygon", fill if t == "polygon" else "#00000000",
                   stroke, border, line_style(obj, scenario and edge))
            if edge:
                bx, by = points[-1]
                ax, ay = points[-2]
                angle = math.atan2(by-ay, bx-ax)
                length = max(2.4, border * 5)
                triangle = [(bx, by), *((bx - length*math.cos(angle + theta),
                                          by - length*math.sin(angle + theta)) for theta in [-.48, .48])]
                yield ("poly", triangle, True, stroke, stroke, 0, [])
                label = f"{obj['edge']} · UNPROVEN" if scenario else obj["label"]
                if label:
                    mid = points[len(points)//2]
                    if len(points) == 2:
                        mid = ((ax + bx) / 2, (ay + by) / 2)
                    label_w = QFontMetricsF(font(8, True)).horizontalAdvance(label) / TEXT_SCALE
                    lx = min(max(mid[0]-label_w/2, 0), max(0, w-label_w))
                    ly = max(3.5, mid[1]-1.7)
                    yield rect(lx-.8, ly-2.8, label_w+1.6, 3.8, "#ffffffee", "#00000000", 0, .5)
                    yield ("text", lx, ly, label, 8, True, False, "#163d39ff")
    if t == "grid":
        for n in (1, 2, 3):
            for coords in ([(w*n/4, 0), (w*n/4, h)], [(0, h*n/4), (w, h*n/4)]):
                yield ("poly", coords, False, "#00000000", stroke, border, [])
    if t == "chart":
        plot_w, plot_h = max(1, w-2*pad), max(1, h-2*pad)
        yield ("poly", [(pad, pad), (pad, h-pad), (w-pad, h-pad)], False, "#00000000", stroke, border, [])
        pts = obj["points"] or [[0, .5]]
        bar_w = plot_w / max(1, len(pts)) * .76
        for at, (_, value) in enumerate(pts):
            yield rect(pad + plot_w * at / len(pts), h - pad - value*plot_h, bar_w, value*plot_h,
                       stroke, stroke, 0, min(.7, bar_w / 2))
    if t == "icon":
        yield ("ellipse", 0, 0, w, h, fill, stroke, border)
        yield ("poly", [(w*.22,h*.52), (w*.43,h*.72), (w*.76,h*.28)], False, "#00000000",
               stroke, max(.5, min(w,h)*.08), [])
    if t == "placeholder":
        yield ("poly", [(0,0), (w,h), (w,0), (0,h)], False, "#00000000", stroke, border, [2, 2])
    if t == "image":
        yield ("image", obj)
    if obj["text"] and t in ("text", "panel", "label", "callout"):
        for y, line in text_lines(obj):
            for x, seg, b, i in line:
                yield ("text", x, y, seg, obj["size"], b, i, obj["ink"])


def pen(stroke, width, dash=()):
    if width <= 0:
        return QPen(Qt.PenStyle.NoPen)
    result = QPen(rgba(stroke))
    result.setWidthF(width)
    result.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    result.setCapStyle(Qt.PenCapStyle.RoundCap)
    if dash:
        result.setDashPattern([max(.05, x/width) for x in dash])
    return result


def draw_local(p, obj, state, sources):
    p.save()
    for cmd in primitives(obj, state):
        tag, v = cmd[0], cmd[1:]
        if tag == "rect":
            x,y,w,h,r,fill,stroke,b = v
            p.setPen(pen(stroke,b))
            p.setBrush(QBrush(rgba(fill)))
            p.drawRoundedRect(QRectF(x,y,w,h),r,r)
        elif tag == "ellipse":
            x,y,w,h,fill,stroke,b = v
            p.setPen(pen(stroke,b))
            p.setBrush(QBrush(rgba(fill)))
            p.drawEllipse(QRectF(x,y,w,h))
        elif tag == "poly":
            points, closed, fill, stroke, b, dash = v
            pts = QPolygonF([QPointF(x,y) for x,y in points])
            p.setPen(pen(stroke,b,dash))
            p.setBrush(QBrush(rgba(fill)))
            (p.drawPolygon if closed else p.drawPolyline)(pts)
        elif tag == "text":
            x,y,text,size,bold,italic,ink = v
            p.save()
            p.setFont(font(size,bold,italic))
            p.setPen(rgba(ink))
            p.scale(1/TEXT_SCALE,1/TEXT_SCALE)
            p.drawText(QPointF(x*TEXT_SCALE,y*TEXT_SCALE), text)
            p.restore()
        elif tag == "image" and v[0]["asset"] in sources:
            data = v[0]
            img = decode_png(sources[data["asset"]], data["asset"])
            p.drawImage(QRectF(0,0,data["w"],data["h"]), img, crop_rect(data,img))
    p.restore()


def page_notice(state):
    if state["topic"] == "hypothesis":
        return SCENARIO_NOTE
    return ""


def notice_object(state):
    from .project import layer
    w, h = state["paper"]["w"], state["paper"]["h"]
    return layer("label", 0, h-7, w, 7, name="Scenario disclosure",
                 fill="#143c38ff", ink="#ffffffff", radius=0, border=0, size=7.2, padding=1.2,
                 text=page_notice(state)) | {"h": 7}


def draw_page(p, state, sources, target, highlight=None, *, only=None):
    w, h = (state["paper"][k] for k in ("w","h"))
    p.save()
    p.translate(target.x(),target.y())
    p.scale(target.width()/w, target.height()/h)
    p.setClipRect(QRectF(0,0,w,h))
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing |
                     QPainter.RenderHint.SmoothPixmapTransform)
    p.fillRect(QRectF(0,0,w,h),rgba(state["paper"]["background"]))
    for key, raw, alpha in front_layers(state):
        if only is not None and key not in only:
            continue
        obj = resolved(raw,state)
        p.save()
        p.setOpacity(alpha)
        p.translate(obj["x"],obj["y"])
        p.rotate(obj["angle"])
        draw_local(p,obj,state,sources)
        if highlight is not None and key in highlight:
            p.setOpacity(1)
            p.setPen(pen("#dc3d72ff", .6, [.75, .6]))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(0,0,obj["w"],obj["h"]))
        p.restore()
    if page_notice(state):
        n = notice_object(state)
        p.translate(n["x"],n["y"])
        draw_local(p,n,state,sources)
    p.restore()


def preview(state, sources, max_side=700, *, highlight=None, only=None):
    w, h = state["paper"]["w"], state["paper"]["h"]
    ratio = max_side / max(w,h)
    img = raster((round(w*ratio),round(h*ratio)))
    with QPainter(img) as p:
        draw_page(p,state,sources,QRectF(img.rect()),highlight,only=only)
    return png_bytes(img)


def print_png(state,sources,dpi):
    require(dpi in (300,600), "PNG export supports 300 or 600 dpi")
    w,h = (round(state["paper"][k]*dpi/25.4) for k in ("w","h"))
    img = raster((w,h),dpi)
    with QPainter(img) as p:
        draw_page(p,state,sources,QRectF(img.rect()))
    return png_bytes(img)


def print_pdf(state,sources):
    out = QBuffer()
    out.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(out)
    writer.setCreator("MAR Studio")
    writer.setTitle("MAR Studio poster")
    writer.setResolution(360)
    mm = QPageSize.Unit.Millimeter
    size = QPageSize(QSizeF(state["paper"]["w"],state["paper"]["h"]),mm,"MAR print",
                     QPageSize.SizeMatchPolicy.ExactMatch)
    require(writer.setPageLayout(QPageLayout(size,QPageLayout.Orientation.Portrait,QMarginsF(),
                                             QPageLayout.Unit.Millimeter)), "PDF page layout was rejected")
    with QPainter(writer) as p:
        require(p.isActive(), "PDF writer did not start")
        draw_page(p,state,sources,QRectF(0,0,writer.width(),writer.height()))
    del writer
    return bytes(out.data())


def nf(x):
    return f"{x:.5f}".rstrip("0").rstrip(".") or "0"


def svg_colors(fill, stroke, width):
    return {"fill":fill[:7],"fill-opacity":nf(int(fill[7:],16)/255),
            "stroke":stroke[:7] if width else "none","stroke-opacity":nf(int(stroke[7:],16)/255),
            "stroke-width":nf(width),"stroke-linejoin":"round","stroke-linecap":"round"}


def svg_export(state,sources):
    w,h = (nf(state["paper"][k]) for k in ("w","h"))
    doc = ET.Element("svg",{"xmlns":"http://www.w3.org/2000/svg",
                            "xmlns:xlink":"http://www.w3.org/1999/xlink",
                            "version":"1.1","width":w+"mm","height":h+"mm","viewBox":f"0 0 {w} {h}"})
    defs = ET.SubElement(doc,"defs")
    clip = ET.SubElement(defs,"clipPath",id="paper")
    ET.SubElement(clip,"rect",width=w,height=h)
    body = ET.SubElement(doc,"g",{"clip-path":"url(#paper)"})
    ET.SubElement(body,"rect",{"width":w,"height":h,
                               **svg_colors(state["paper"]["background"], "#00000000", 0)})
    def obj_svg(key,obj,alpha,parent):
        group = ET.SubElement(parent,"g",{"id":"layer-"+key,"opacity":nf(alpha),
                              "transform":f"translate({nf(obj['x'])} {nf(obj['y'])}) rotate({nf(obj['angle'])})"})
        if obj["alt"]:
            ET.SubElement(group,"desc").text = obj["alt"]
        for kind,*v in primitives(obj,state):
            if kind == "rect":
                x,y,rw,rh,r,fill,stroke,b = v
                ET.SubElement(group,"rect",{"x":nf(x),"y":nf(y),"width":nf(rw),"height":nf(rh),"rx":nf(r),
                                            **svg_colors(fill,stroke,b)})
            elif kind == "ellipse":
                x,y,rw,rh,f,s,b = v
                ET.SubElement(group,"ellipse",{"cx":nf(x+rw/2),"cy":nf(y+rh/2),
                                               "rx":nf(rw/2),"ry":nf(rh/2),**svg_colors(f,s,b)})
            elif kind == "poly":
                points,closed,f,s,b,dash = v
                attrs = {"points":" ".join(f"{nf(x)},{nf(y)}" for x,y in points),**svg_colors(f,s,b)}
                if dash:
                    attrs["stroke-dasharray"] = " ".join(nf(x) for x in dash)
                if not closed:
                    attrs["fill"] = "none"
                ET.SubElement(group,"polygon" if closed else "polyline",attrs)
            elif kind == "text":
                x,y,txt,size,bold,italic,ink = v
                # Separate style runs use the same physical baselines and font size as print.
                ET.SubElement(group,"text",{"x":nf(x),"y":nf(y),"font-family":FACE,
                                            "font-size":nf(font(size,bold,italic).pixelSize()/TEXT_SCALE),
                                            "font-weight":"bold" if bold else "normal",
                                            "font-style":"italic" if italic else "normal",
                                            "xml:space":"preserve",**svg_colors(ink,"#00000000",0)}).text = txt
            elif kind == "image":
                obj = v[0]
                img = decode_png(sources[obj["asset"]], obj["asset"])
                crop = crop_rect(obj,img).toAlignedRect().intersected(img.rect())
                payload = base64.b64encode(fresh_png(img.copy(crop))).decode("ascii")
                ET.SubElement(group,"image",{"width":nf(obj["w"]),"height":nf(obj["h"]),
                                            "preserveAspectRatio":"none","xlink:href":"data:image/png;base64,"+payload})
    for key,obj,alpha in front_layers(state):
        obj_svg(key,resolved(obj,state),alpha,body)
    if page_notice(state):
        obj_svg("notice",notice_object(state),1,body)
    return ET.tostring(doc,encoding="utf-8",xml_declaration=True)

