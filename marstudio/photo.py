"""Pixel operations and temporary undo history for one placed PNG."""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from .integrity import digest, png_dimensions, require

MAX_EDIT_PIXELS = 20_000_000
UNDO_BYTES = 128 * 1024**2
PASTE_TOOL = "Paste pixels into layer"


@dataclass(frozen=True)
class PixelClip:
    image: Image.Image
    source_assets: tuple[str, ...]
    sha256: str

    @classmethod
    def capture(cls, image, source_assets):
        return cls(image, tuple(source_assets), digest(encode_pixels(image)))


@dataclass
class PixelClipboard:
    clip: PixelClip | None = None


def pasted_sources(operations):
    return sorted({key for entry in operations if entry["tool"] == PASTE_TOOL
                   for key in entry["source_assets"]})


def open_pixels(data, crop):
    width, height = png_dimensions(data)
    x, y, w, h = crop
    size = (max(1, round(w * width)), max(1, round(h * height)))
    require(size[0] * size[1] <= MAX_EDIT_PIXELS,
            "Pixel editing supports up to 20 megapixels per layer. Use Image Region to edit a smaller area.")
    bounds = (x * width, y * height, (x + w) * width, (y + h) * height)
    with Image.open(io.BytesIO(data), formats=("PNG",)) as source:
        if all(abs(v - round(v)) < 0.00001 for v in bounds):
            result = source.crop(tuple(round(v) for v in bounds)).convert("RGBA")
        else:
            result = source.convert("RGBA").transform(size, Image.Transform.EXTENT, bounds,
                                                      Image.Resampling.BILINEAR)
    result.info.clear()
    return result


def encode_pixels(image):
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def levels(image, black, white, gamma, saturation, channel):
    table = [round(255 * pow(min(1, max(0, (value - black) / (white - black))), 1 / gamma))
             for value in range(256)]
    rgb = image.convert("RGB")
    if channel == "RGB":
        rgb = rgb.point(table * 3)
    else:
        bands = list(rgb.split())
        index = ("Red", "Green", "Blue").index(channel)
        bands[index] = bands[index].point(table)
        rgb = Image.merge("RGB", bands)
    if saturation != 100:
        rgb = ImageEnhance.Color(rgb).enhance(saturation / 100)
    rgb.putalpha(image.getchannel("A"))
    return rgb


def filtered(image, name, radius, strength):
    if name == "Gaussian blur":
        return image.convert("RGBa").filter(ImageFilter.GaussianBlur(radius)).convert("RGBA")
    if name == "Unsharp mask":
        rgb = image.convert("RGB").filter(ImageFilter.UnsharpMask(radius, strength, 3))
    else:
        rgb = ImageOps.invert(image.convert("RGB"))
    rgb.putalpha(image.getchannel("A"))
    return rgb


def selection_mask(size, points, rectangle, feather):
    mask = Image.new("L", size)
    draw = ImageDraw.Draw(mask)
    if rectangle:
        (x1, y1), (x2, y2) = points
        draw.rectangle((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), fill=255)
    else:
        draw.polygon(points, fill=255)
    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(feather))
    return mask


def color_selection(image, point, tolerance):
    rgb = image.convert("RGB")
    channels = rgb.split()
    picked = rgb.getpixel(point)
    table = [255 if value <= tolerance else 0 for value in range(256)]
    matches = [ImageChops.difference(band, Image.new("L", image.size, value)).point(table)
               for band, value in zip(channels, picked)]
    return ImageChops.darker(matches[0], ImageChops.darker(matches[1], matches[2]))


def masked_pixels(image, mask):
    result = image.copy()
    result.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
    return result


def lift_selection(image, mask):
    """Return a tight cutout and its original location on the bitmap."""
    bounds = ImageChops.multiply(image.getchannel("A"), mask).getbbox()
    if bounds is None:
        return None
    return masked_pixels(image.crop(bounds), mask.crop(bounds)), bounds


def erase_mask(image, mask):
    return masked_pixels(image, ImageOps.invert(mask))


def move_pixels(remainder, lifted, position):
    result = remainder.copy()
    result.alpha_composite(lifted, dest=position)
    return result


def paste_pixels(target, image, position, size, angle, *, opacity=100, flip_x=False, flip_y=False):
    """Composite a clockwise rotation around the scaled selection's center."""
    if opacity == 0:
        return target
    if flip_x:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if flip_y:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if opacity < 100:
        image = image.copy()
        image.putalpha(image.getchannel("A").point([round(a * opacity / 100) for a in range(256)]))
    if size == image.size and angle == 0:
        return move_pixels(target, image, position)
    width, height = size
    center_x, center_y = position[0] + width / 2, position[1] + height / 2
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    half_width = (abs(cosine) * width + abs(sine) * height) / 2
    half_height = (abs(sine) * width + abs(cosine) * height) / 2
    left = max(0, math.floor(center_x - half_width))
    top = max(0, math.floor(center_y - half_height))
    right = min(target.width, math.ceil(center_x + half_width))
    bottom = min(target.height, math.ceil(center_y + half_height))
    if right <= left or bottom <= top:
        return target
    # Sample in original pixel coordinates, allocating only the part inside the target.
    scale_x, scale_y = image.width / width, image.height / height
    x, y = left - center_x, top - center_y
    inverse = (
        cosine * scale_x, sine * scale_x, (cosine * x + sine * y + width / 2) * scale_x,
        -sine * scale_y, cosine * scale_y, (-sine * x + cosine * y + height / 2) * scale_y,
    )
    fragment = image.transform((right - left, bottom - top), Image.Transform.AFFINE, inverse,
                               Image.Resampling.BICUBIC, fillcolor=(0, 0, 0, 0))
    return move_pixels(target, fragment, (left, top))


@lru_cache(maxsize=32)
def brush_mask(size, hardness, opacity):
    radius = size / 2
    edge = max(0.5, radius * (1 - hardness / 100))
    values = [
        round(255 * opacity / 100 * min(1, max(0, (radius - math.hypot(
            x + 0.5 - radius, y + 0.5 - radius)) / edge)))
        for y in range(size) for x in range(size)
    ]
    return Image.frombytes("L", (size, size), bytes(values))


class PhotoEdits:
    def __init__(self, image):
        self.original = image
        self.image = image
        self.operations = []
        self.undo_stack = []
        self.redo_stack = []
        self.before_stroke = None

    @staticmethod
    def _push(stack, image, detail):
        stack.append((image, detail))
        limit = max(1, min(24, UNDO_BYTES // (4 * image.width * image.height)))
        del stack[:-limit]

    def _remember(self, previous, detail):
        self._push(self.undo_stack, previous, detail)
        self.redo_stack.clear()
        self.operations.append(detail)

    def apply(self, result, detail, selection=None):
        if selection is not None:
            result = Image.composite(result, self.image, selection)
        if result.size == self.image.size and not ImageChops.difference(result, self.image).getbbox(alpha_only=False):
            return
        self._remember(self.image, detail)
        self.image = result

    def undo(self):
        if self.undo_stack:
            previous, detail = self.undo_stack.pop()
            self._push(self.redo_stack, self.image, detail)
            self.image = previous
            self.operations.pop()

    def redo(self):
        if self.redo_stack:
            result, detail = self.redo_stack.pop()
            self._push(self.undo_stack, self.image, detail)
            self.image = result
            self.operations.append(detail)

    def changed(self):
        return self.image.size != self.original.size or bool(
            ImageChops.difference(self.image, self.original).getbbox(alpha_only=False))

    def begin_stroke(self):
        self.before_stroke = self.image
        self.image = self.image.copy()
        self.stroke_changed = False

    def stamp(self, point, tool, size, hardness, opacity, rgb, selection, offset):
        mask = brush_mask(size, hardness, opacity)
        left, top = math.floor(point[0] - (size - 1) / 2), math.floor(point[1] - (size - 1) / 2)
        right, bottom = left + size, top + size
        box = (max(0, left), max(0, top), min(self.image.width, right), min(self.image.height, bottom))
        x1, y1, x2, y2 = box
        if x1 >= x2 or y1 >= y2:
            return None
        mask = mask.crop((x1 - left, y1 - top, x2 - left, y2 - top))
        if selection is not None:
            mask = ImageChops.multiply(mask, selection.crop(box))
        destination = self.image.crop(box)
        if tool == "Eraser":
            result = destination.copy()
            result.putalpha(ImageChops.multiply(destination.getchannel("A"), ImageOps.invert(mask)))
        elif tool == "Clone stamp":
            dx, dy = offset
            source_box = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
            source = self.before_stroke.crop(source_box)
            valid = Image.new("L", mask.size)
            sx1, sy1 = max(0, -source_box[0]), max(0, -source_box[1])
            sx2 = min(mask.width, self.image.width - source_box[0])
            sy2 = min(mask.height, self.image.height - source_box[1])
            if sx1 >= sx2 or sy1 >= sy2:
                return None
            ImageDraw.Draw(valid).rectangle((sx1, sy1, sx2 - 1, sy2 - 1), fill=255)
            result = Image.composite(source, destination, ImageChops.multiply(mask, valid))
        else:
            color = Image.new("RGBA", mask.size, rgb)
            color.putalpha(mask)
            result = Image.alpha_composite(destination, color)
        if not ImageChops.difference(result, destination).getbbox(alpha_only=False):
            return None
        self.image.paste(result, (x1, y1))
        self.stroke_changed = True
        return box

    def end_stroke(self, detail):
        if self.stroke_changed:
            self._remember(self.before_stroke, detail)
        else:
            self.image = self.before_stroke
        self.before_stroke = None


def stroke_points(start, end, spacing):
    dx, dy = end[0] - start[0], end[1] - start[1]
    steps = max(1, math.ceil(math.hypot(dx, dy) / spacing))
    return ((start[0] + dx * i / steps, start[1] + dy * i / steps) for i in range(1, steps + 1))
