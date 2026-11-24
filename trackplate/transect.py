"""Use a marked origin, axis, and physical scale for a true transverse profile."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid, integer, number


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024


def png(image):
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def decode(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise Invalid("单张原片不得超过18 MiB")
    try:
        with Image.open(BytesIO(data)) as photo:
            if photo.format not in {"PNG", "JPEG", "TIFF", "BMP", "WEBP"} or getattr(photo, "n_frames", 1) != 1:
                raise Invalid("请选择单帧 PNG、JPEG、TIFF、BMP 或 WebP")
            if min(photo.size) < 32 or max(photo.size) > 2600 or math.prod(photo.size) > 4_000_000:
                raise Invalid("原片边长须在32–2600 px内，总像素不超过400万")
            normal = ImageOps.exif_transpose(photo).convert("RGB")
            return normal.size, png(normal)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise Invalid("照片无法解码") from error


def pixels(path):
    with Image.open(path) as photo:
        return np.asarray(photo.convert("RGB"))


def settings(value, size):
    w, h = size
    limit = max(size)
    return {
        "cx": number(value.get("cx"), "参考原点 X", 0, w - 1),
        "cy": number(value.get("cy"), "参考原点 Y", 0, h - 1),
        "angle": number(value.get("angle"), "印迹前向角度", 0, 360) % 360,
        "station": integer(value.get("station"), "前后截位", -limit, limit),
        "half_span": integer(value.get("half_span"), "横截取样半宽", 3, limit),
        "cutoff": integer(value.get("cutoff"), "印痕亮度上限", 1, 254),
        "mm_per_px": number(value.get("mm_per_px"), "尺寸换算", 0.001, 500),
    }


def defaults(size):
    w, h = size
    return settings({"cx": w // 2, "cy": h * 0.56, "angle": 270, "station": 0,
                     "half_span": max(3, min(size)//5), "cutoff": 148, "mm_per_px": 1}, size)


def basis(config):
    rad = math.radians(config["angle"])
    forward = (math.cos(rad), math.sin(rad))
    return forward, (-forward[1], forward[0])


def point(image, x, y, config):
    forward, cross = basis(config)
    dx, dy = x - config["cx"], y - config["cy"]
    rgb = image[y, x]
    brightness = float(rgb @ LUMA)
    return {"x": x, "y": y, "longitudinal": dx*forward[0] + dy*forward[1],
            "transverse": dx*cross[0] + dy*cross[1], "rgb": [int(value) for value in rgb],
            "brightness": brightness, "marked": bool(brightness <= config["cutoff"])}


def profile(image, config):
    forward, cross = basis(config)
    cx = config["cx"] + forward[0] * config["station"]
    cy = config["cy"] + forward[1] * config["station"]
    offsets = np.arange(-config["half_span"], config["half_span"] + 1)
    xs = np.rint(cx + offsets*cross[0]).astype(int)
    ys = np.rint(cy + offsets*cross[1]).astype(int)
    h, w = image.shape[:2]
    valid = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    levels = image[ys[valid], xs[valid]] @ LUMA
    values = []
    for offset, x, y, level in zip(offsets[valid], xs[valid], ys[valid], levels):
        values.append({"offset": int(offset), "x": int(x), "y": int(y),
                       "brightness": float(level), "marked": bool(level <= config["cutoff"])})
    return values, {"x": cx, "y": cy}, cross


def longest_mark(values):
    """Return the widest single uninterrupted dark span on the current transect."""
    best = []
    current = []
    for sample in values:
        if not sample["marked"]:
            current = []
            continue
        if current and sample["offset"] != current[-1]["offset"] + 1:
            current = []
        current.append(sample)
        if len(current) > len(best):
            best = current.copy()
    return best


def report(image, config):
    values, center, across = profile(image, config)
    span = longest_mark(values)
    reading = None
    if span:
        width = span[-1]["offset"] - span[0]["offset"] + 1
        reading = {"pixels": width, "width_mm": width*config["mm_per_px"], "ends": [span[0], span[-1]]}
    return {"reading": reading, "profile": values, "center": center, "across": across,
            "angle": config["angle"], "station": config["station"]}


def practice():
    w, h = 746, 486
    cfg = settings({"cx": 374, "cy": 287, "angle": 270, "station": 0,
                    "half_span": 118, "cutoff": 155, "mm_per_px": 0.8}, (w, h))
    yy, xx = np.indices((h, w))
    roughness = 3 * np.sin(xx*.39 + yy*.34) + 2 * np.cos(xx*.67 - yy*.53) + 1.7*np.sin(yy*.8)
    base = 199 + 18*np.cos((xx-160)/350) - yy*.032 + roughness
    marks = [
        (374, 287, 72, 52, 0.10),
        (324, 269, 31, 26, -.4),
        (425, 264, 33, 29, .50),
        (300, 206, 21, 33, -.32),
        (348, 174, 22, 34, -.13),
        (402, 170, 21, 34, .15),
        (453, 198, 22, 30, .38),
    ]
    shaping = np.zeros_like(base)
    for cx, cy, rx, ry, rotation in marks:
        dx, dy = xx-cx, yy-cy
        u = (dx*math.cos(rotation) + dy*math.sin(rotation))/rx
        v = (-dx*math.sin(rotation) + dy*math.cos(rotation))/ry
        norm = u*u + v*v + .04*np.sin(xx*.22 + yy*.13)
        shaping = np.maximum(shaping, np.clip((1-norm)*2.2,0,1))
    edge = np.maximum(0, shaping*(1-shaping))
    imprint = base - 100*shaping + 20*edge
    bands = [imprint + amount for amount in (14, 4, -12)]
    photo = Image.fromarray(np.stack(bands, axis=-1).clip(0,255).astype("uint8"), "RGB")
    draw = ImageDraw.Draw(photo)
    draw.text((29,62), "CAST STUDY / A", fill="#514739", font=ImageFont.load_default(size=17))
    draw.text((29,410), "SIMULATED IMPRESSION", fill="#695a48", font=ImageFont.load_default(size=11))
    for offset in range(0,101,10):
        draw.line((47+offset,435,47+offset,428 if offset % 50 else 422), fill="#584f3c", width=2)
    draw.line((47,435,147,435), fill="#584f3c", width=2)
    draw.text((151,424), "80 mm", fill="#584f3c", font=ImageFont.load_default(size=12))
    return photo.size, png(photo), cfg
