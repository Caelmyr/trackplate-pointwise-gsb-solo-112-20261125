"""Use a marked origin, axis, and physical scale for a true transverse profile."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from scipy import ndimage as ndi

from image_lab.validation import Invalid, integer, number


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024
AXIS_LIMIT = 35.0
AXIS_STEP = 0.5
MIN_COMPONENT = 12
TOE_ELONGATION = 3.0
TOE_FILL = 0.35
TOE_PAD_RATIO = 0.02
SIDE_GAP = 3.0


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
        "lateral": integer(value.get("lateral", 0), "侧向截位", -limit, limit),
        "half_span": integer(value.get("half_span"), "横截取样半宽", 3, limit),
        "cutoff": integer(value.get("cutoff"), "印痕亮度上限", 1, 254),
        "mm_per_px": number(value.get("mm_per_px"), "尺寸换算", 0.001, 500),
    }


def defaults(size):
    w, h = size
    return settings({"cx": w // 2, "cy": h * 0.56, "angle": 270, "station": 0, "lateral": 0,
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
    cx = config["cx"] + forward[0] * config["station"] + cross[0] * config["lateral"]
    cy = config["cy"] + forward[1] * config["station"] + cross[1] * config["lateral"]
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
            "angle": config["angle"], "station": config["station"], "lateral": config["lateral"]}


def adopt_axis(config, angle):
    """Rotate onto a new axis while keeping the marked transect center at the same photo pixel."""
    forward, cross = basis(config)
    center_x = config["cx"] + forward[0]*config["station"] + cross[0]*config["lateral"]
    center_y = config["cy"] + forward[1]*config["station"] + cross[1]*config["lateral"]
    rad = math.radians(angle % 360)
    new_forward = (math.cos(rad), math.sin(rad))
    new_cross = (-new_forward[1], new_forward[0])
    dx, dy = center_x - config["cx"], center_y - config["cy"]
    station = int(round(dx*new_forward[0] + dy*new_forward[1]))
    lateral = int(round(dx*new_cross[0] + dy*new_cross[1]))
    return {**config, "angle": angle % 360, "station": station, "lateral": lateral}


def _window_mask(shape, config):
    """Rectangular search window: origin to 2*half_span forward, one half_span each side."""
    height, width = shape
    forward, cross = basis(config)
    yy, xx = np.indices((height, width))
    dx = xx - config["cx"]
    dy = yy - config["cy"]
    longitudinal = dx*forward[0] + dy*forward[1]
    transverse = dx*cross[0] + dy*cross[1]
    span = config["half_span"]
    return ((longitudinal >= 0) & (longitudinal <= 2*span)
            & (np.abs(transverse) <= span))


def _components(image, config):
    luma = image @ LUMA
    dark = (luma <= config["cutoff"]) & _window_mask(luma.shape, config)
    labels, total = ndi.label(dark, structure=np.ones((3, 3)))
    origin_label = labels[min(int(round(config["cy"])), luma.shape[0] - 1),
                           min(int(round(config["cx"])), luma.shape[1] - 1)]
    rows = []
    for index in range(1, total + 1):
        ys, xs = np.nonzero(labels == index)
        area = len(xs)
        if area < MIN_COMPONENT:
            continue
        eigenvalues = np.linalg.eigvalsh(np.cov(np.stack([xs.astype(float), ys.astype(float)])))
        elongation = math.sqrt(eigenvalues[1] / max(eigenvalues[0], 1e-9))
        box_w = xs.max() - xs.min() + 1
        box_h = ys.max() - ys.min() + 1
        rows.append({"id": index, "area": area, "elongation": elongation,
                     "fill": area / (box_w * box_h), "pad": index == origin_label,
                     "cx": float(xs.mean()), "cy": float(ys.mean()),
                     "xs": xs, "ys": ys})
    return rows, int(origin_label)


def _component_summary(component):
    return {"id": component["id"], "area": component["area"],
            "elongation": round(component["elongation"], 2), "fill": round(component["fill"], 2),
            "centroid_x": round(component["cx"], 1), "centroid_y": round(component["cy"], 1)}


def _frame(config, angle):
    rad = math.radians(angle)
    forward = (math.cos(rad), math.sin(rad))
    cross = (-forward[1], forward[0])
    return forward, cross


def _project(component, config, forward, cross):
    dx = component["cx"] - config["cx"]
    dy = component["cy"] - config["cy"]
    return dx*forward[0] + dy*forward[1], dx*cross[0] + dy*cross[1]


def _score_angle(angle, config, toes, pad):
    forward, cross = _frame(config, angle)
    transverse = np.array([_project(toe, config, forward, cross)[1] for toe in toes])
    left = transverse < -SIDE_GAP
    right = transverse > SIDE_GAP
    if not (left.any() and right.any()):
        return None
    toe_bias = abs(float(transverse.mean()))
    # Real-shot symmetry of the pad front edge: mirror balance inside forward bins.
    px = pad["xs"].astype(float) - config["cx"]
    py = pad["ys"].astype(float) - config["cy"]
    longitudinal = px*forward[0] + py*forward[1]
    front = np.percentile(longitudinal, 92)
    selected = longitudinal >= front - 0.55*config["half_span"]
    across = px[selected]*cross[0] + py[selected]*cross[1]
    bins = np.linspace(longitudinal[selected].min(), longitudinal[selected].max() + 1e-9, 9)
    bucket = np.digitize(longitudinal[selected], bins) - 1
    edge_means = []
    for bucket_id in range(8):
        members = across[bucket == bucket_id]
        if len(members) >= 6:
            edge_means.append(members.mean())
    edge_bias = float(np.abs(edge_means).mean()) if edge_means else float("nan")
    return {"angle": angle, "toe_bias": toe_bias, "edge_bias": edge_bias,
            "left": int(left.sum()), "right": int(right.sum())}


def suggest_axis(image, config):
    """Propose a toe-pointing axis from real marks; return review reasons when evidence is weak."""
    components, origin_label = _components(image, config)
    base = {"coarse_angle": config["angle"], "limit_deg": AXIS_LIMIT,
            "window": {"forward_px": 2 * config["half_span"], "side_px": config["half_span"]}}
    if origin_label == 0:
        return {**base, "status": "review", "angle": None,
                "reasons": ["参考原点没有落在亮度门槛内的实见印痕上，未见含原点的掌垫，请复核原点与亮度上限。"]}
    pad = next((component for component in components if component["pad"]), None)
    if pad is None:
        return {**base, "status": "review", "angle": None,
                "reasons": ["原点所在暗区过小，未形成可辨认的掌垫，请复核原点位置。"]}
    toes, scratches = [], []
    for component in components:
        if component["pad"]:
            continue
        if component["elongation"] > TOE_ELONGATION or component["fill"] < TOE_FILL:
            scratches.append(component)
        elif component["area"] >= TOE_PAD_RATIO * pad["area"]:
            toes.append(component)
    base["pad"] = _component_summary(pad)
    base["toes"] = [_component_summary(toe) for toe in toes]
    base["scratches_excluded"] = [_component_summary(mark) for mark in scratches]
    if not toes:
        return {**base, "status": "review", "angle": None,
                "reasons": ["窗口内没有与掌垫分开的紧凑趾痕，细长划痕已剔除，请复核粗前向、取样半宽或亮度上限。"]}
    # Side counts must hold for the winning angle, but an obviously one-sided field fails fast.
    coarse_forward, coarse_cross = basis(config)
    coarse_sides = np.array([_project(toe, config, coarse_forward, coarse_cross)[1] for toe in toes])
    if not ((coarse_sides < -SIDE_GAP).any() and (coarse_sides > SIDE_GAP).any()):
        return {**base, "status": "review", "angle": None,
                "reasons": [f"粗前向下仅在一侧见到紧凑趾痕（左 {int((coarse_sides < -SIDE_GAP).sum())} 枚、"
                            f"右 {int((coarse_sides > SIDE_GAP).sum())} 枚），按建议前向须左右各至少一枚，请复核。"]}
    candidates = []
    start = config["angle"] - AXIS_LIMIT
    for step in range(int(2 * AXIS_LIMIT / AXIS_STEP) + 1):
        angle = (start + step * AXIS_STEP) % 360
        scored = _score_angle(angle, config, toes, pad)
        if scored is not None:
            candidates.append(scored)
    if not candidates:
        return {**base, "status": "review", "angle": None,
                "reasons": ["±35° 内没有候选角能让左右两侧各保留一枚趾痕，请复核粗前向。"]}
    toe_scale = max(min(row["toe_bias"] for row in candidates), 1e-9)
    finite_edges = [row["edge_bias"] for row in candidates if math.isfinite(row["edge_bias"])]
    edge_scale = min(finite_edges) if finite_edges else None
    for row in candidates:
        edge_term = row["edge_bias"] / edge_scale if edge_scale else 0.0
        row["cost"] = 0.6 * row["toe_bias"] / toe_scale + 0.4 * edge_term

    def wrap_diff(angle):
        return abs((angle - config["angle"] + 180) % 360 - 180)

    best = min(candidates, key=lambda row: (row["cost"], wrap_diff(row["angle"])))
    chosen = round(best["angle"], 2)
    delta = (chosen - config["angle"] + 180) % 360 - 180
    adoption = adopt_axis(config, chosen)
    return {**base, "status": "ok", "angle": chosen, "delta_deg": round(delta, 2),
            "left_toes": best["left"], "right_toes": best["right"],
            "toe_bias_px": round(best["toe_bias"], 2),
            "edge_bias_px": round(best["edge_bias"], 2) if math.isfinite(best["edge_bias"]) else None,
            "adoption": {"angle": adoption["angle"], "station": adoption["station"],
                         "lateral": adoption["lateral"]},
            "reasons": []}


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
