"""Use a marked origin, axis, and physical scale for a true transverse profile."""

from io import BytesIO
import math

import numpy as np
from scipy import ndimage as ndi
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid, choice, integer, number


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024
FOUR_CONNECTED = ndi.generate_binary_structure(2, 1)
SEAM_LIMIT = 3          # a light seam may not be wider than this many source pixels
MIN_CONTACT = 2         # a facing edge needs more than a single touching pixel
MIN_FACING = 0.30       # both edges must actually face across the seam
MIN_BLOCK_PIXELS = 3    # smaller dark specks are print noise, not palm blocks
MIN_REPORTED_PIXELS = 8
SAME_SEAM_SPAN = 2 * SEAM_LIMIT + 2
READING_MODES = ("pad", "continuous")


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
        "reading_mode": choice(value.get("reading_mode") or "pad", "读数方式", READING_MODES),
    }


def defaults(size):
    w, h = size
    return settings({"cx": w // 2, "cy": h * 0.56, "angle": 270, "station": 0,
                     "half_span": max(3, min(size)//5), "cutoff": 148, "mm_per_px": 1,
                     "reading_mode": "pad"}, size)


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


def analysis_window(config, shape):
    """Square window: one sampling half-width forward/back and left/right of the origin."""
    height, width = shape[:2]
    forward, cross = basis(config)
    half = config["half_span"]
    fx, fy = forward
    tx, ty = cross
    span_x = half * (abs(fx) + abs(tx))
    span_y = half * (abs(fy) + abs(ty))
    x0 = max(0, int(math.floor(config["cx"] - span_x - 1e-9)))
    x1 = min(width, int(math.ceil(config["cx"] + span_x + 1e-9)) + 1)
    y0 = max(0, int(math.floor(config["cy"] - span_y - 1e-9)))
    y1 = min(height, int(math.ceil(config["cy"] + span_y + 1e-9)) + 1)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    dx = xx - config["cx"]
    dy = yy - config["cy"]
    inside = ((np.abs(dx*fx + dy*fy) <= half + 1e-9) &
              (np.abs(dx*tx + dy*ty) <= half + 1e-9))
    return inside, (x0, y0, x1, y1)


def seam_gap(base_pixel, block_pixel, labels, base_label, block_label):
    """Count unprinted source pixels between two facing edge pixels.

    ``None`` means the straightest connection crosses a third dark block, so a
    far block is not allowed to reach through an already accepted piece.
    """
    bx, by = base_pixel
    px, py = block_pixel
    steps = max(abs(px - bx), abs(py - by))
    if steps <= 1:
        return 0
    gap = 0
    seen = set()
    for step in range(1, steps):
        x = int(round(bx + (px - bx) * step / steps))
        y = int(round(by + (py - by) * step / steps))
        if (x, y) in seen:
            continue
        seen.add((x, y))
        label = int(labels[y, x])
        if label == 0:
            gap += 1
        elif label not in (base_label, block_label):
            return None
    return gap


def _normal(gradient, x, y):
    """Outward normal at an edge pixel; ``gradient`` comes from ``np.gradient`` (dy, dx)."""
    gy, gx = gradient
    vector = np.array([gx[y, x], gy[y, x]], dtype=float)
    length = float(np.hypot(*vector))
    return vector / length if length else vector


def pad_analysis(image, config):
    """Grow the palm pad from the origin by facing edges across narrow light seams.

    Everything is computed inside the square sampling window only. The returned
    pad mask is window-local; ``area["region"]`` gives its source offset.
    """
    brightness = image @ LUMA
    marked = brightness <= config["cutoff"]
    inside, (x0, y0, x1, y1) = analysis_window(config, image.shape)
    panel = marked[y0:y1, x0:x1] & inside
    labels, count = ndi.label(panel, structure=FOUR_CONNECTED)
    pad = np.zeros(panel.shape, dtype=bool)
    area = {"pixels": 0, "mm2": 0.0, "origin_marked": False,
            "base_pixels": 0, "region": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
            "blocks": []}
    if not count:
        return pad, area

    origin_x = min(image.shape[1] - 1, max(0, int(round(config["cx"]))))
    origin_y = min(image.shape[0] - 1, max(0, int(round(config["cy"]))))
    local_x, local_y = origin_x - x0, origin_y - y0
    base_label = 0
    if 0 <= local_y < labels.shape[0] and 0 <= local_x < labels.shape[1]:
        base_label = int(labels[local_y, local_x])
        area["origin_marked"] = base_label > 0
    if base_label == 0:
        return pad, area

    base = labels == base_label
    pad |= base
    area["base_pixels"] = int(base.sum())

    distance, nearest = ndi.distance_transform_edt(~base, return_indices=True)
    base_gradient = np.gradient(distance)
    objects = ndi.find_objects(labels)
    areas = np.bincount(labels.ravel(), minlength=count + 1)
    found = np.arange(1, count + 1)
    closest_by_label = np.asarray(ndi.minimum(distance, labels, index=found)).ravel()
    centers = ndi.center_of_mass(panel, labels, found)
    examined = []
    separated = 0
    for block_label in range(1, count + 1):
        if block_label == base_label:
            continue
        pixels = int(areas[block_label])
        if pixels < MIN_BLOCK_PIXELS:
            continue
        closest = float(closest_by_label[block_label - 1])
        estimated_gap = max(0, int(math.ceil(closest)) - 1)
        center_y, center_x = centers[block_label - 1]
        center = [int(round(center_x)) + x0, int(round(center_y)) + y0]
        if closest > SEAM_LIMIT + 2:
            separated += 1
            if pixels >= MIN_REPORTED_PIXELS:
                examined.append({"pixels": pixels, "gap": estimated_gap,
                                 "attached": False, "reason": "seam_too_wide", "center": center})
            continue
        window = objects[block_label - 1]
        top, bottom = window[0].start, window[0].stop
        left, right = window[1].start, window[1].stop
        local_ys, local_xs = np.nonzero(labels[window] == block_label)
        separation = distance[local_ys + top, local_xs + left]

        # Facing edge pairs between this block and the origin block.
        nearest_points = []
        for index in np.nonzero(separation <= closest + 0.5)[0]:
            y = int(local_ys[index]) + top
            x = int(local_xs[index]) + left
            nearest_points.append((int(nearest[1, y, x]), int(nearest[0, y, x]), x, y))

        # Local edge normals, so both physical edges can be compared across the seam.
        # A two-pixel background ring keeps the gradient pointing out of the block.
        top = max(0, top - 2)
        bottom = min(labels.shape[0], bottom + 2)
        left = max(0, left - 2)
        right = min(labels.shape[1], right + 2)
        block = labels[top:bottom, left:right] == block_label
        block_gradient = np.gradient(ndi.distance_transform_edt(~block))

        contact_points = set()
        facings = []
        gaps = []
        wide = []
        crossed = False
        for bx, by, px, py in set(nearest_points):
            gap = seam_gap((bx, by), (px, py), labels, base_label, block_label)
            if gap is None:
                crossed = True
                continue
            if gap > SEAM_LIMIT:
                wide.append(gap)
                continue
            direction = np.array([px - bx, py - by], dtype=float)
            length = float(np.hypot(*direction))
            if not length:
                continue
            direction /= length
            outward_base = _normal(base_gradient, bx, by)
            outward_block = _normal(block_gradient, px - left, py - top)
            facing = max(0.0, float(outward_base @ direction)) * max(0.0, float(-outward_block @ direction))
            contact_points.add((bx, by))
            facings.append(facing)
            gaps.append(gap)

        if not contact_points:
            if wide:
                reason, estimated_gap = "seam_too_wide", max(wide)
            elif crossed:
                reason = "beyond_attached_piece"
            else:
                reason = "edges_do_not_face"
            examined.append({"pixels": pixels, "gap": estimated_gap,
                             "attached": False, "reason": reason, "center": center})
            continue

        contact = len(contact_points)
        facing = float(np.mean(facings))
        gap = max(gaps)
        # Favour edges that fit along their facing sides and cross little unprinted space.
        score = contact * facing / (1 + gap)
        examined.append({"label": block_label, "pixels": pixels, "gap": int(gap),
                         "contact": contact, "facing": facing, "score": score,
                         "center": center, "attached": False, "reason": None,
                         "contact_points": sorted(contact_points), "window": window})

    candidates = [block for block in examined if block.get("label") is not None]
    candidates.sort(key=lambda block: (-block["score"], -block.get("contact", 0),
                                       block.get("gap", 0), -block["pixels"],
                                       block["label"]))
    accepted = []
    for block in candidates:
        if block.get("contact", 0) < MIN_CONTACT or block.get("facing", 0) < MIN_FACING:
            block["reason"] = "edge_not_facing"
            continue
        points = np.asarray(block["contact_points"], dtype=float)
        conflict = False
        for attached in accepted:
            other = np.asarray(attached["contact_points"], dtype=float)
            span = np.abs(points[:, None, :] - other[None, :, :]).max(axis=2)
            if float(span.min()) <= SAME_SEAM_SPAN:
                conflict = True
                break
        if conflict:
            block["reason"] = "same_seam"
            continue
        block["attached"] = True
        accepted.append(block)

    for block in accepted:
        window = block["window"]
        pad[window] |= labels[window] == block["label"]

    for block in examined:
        block.pop("window", None)
        block.pop("contact_points", None)
        block.pop("label", None)
        block["facing"] = round(float(block.get("facing", 0)), 3)
        block["score"] = round(float(block.get("score", 0)), 3) if "score" in block else None
    examined.sort(key=lambda block: (not block["attached"], -block["pixels"]))
    area.update(pixels=int(pad.sum()), mm2=float(pad.sum()) * config["mm_per_px"] ** 2,
                blocks=examined[:24], separated=separated)
    return pad, area


def pad_runs(pad, region):
    """Compact row runs of the accepted pad, in source pixels, for the proof overlay."""
    runs = []
    for row, start, stop in _runs(pad):
        runs.append([row + region["y0"], start + region["x0"], stop + region["x0"]])
    return runs


def _runs(pad):
    runs = []
    for row in np.nonzero(pad.any(axis=1))[0]:
        columns = np.nonzero(pad[row])[0]
        start = previous = int(columns[0])
        for column in columns[1:]:
            column = int(column)
            if column != previous + 1:
                runs.append((int(row), start, previous + 1))
                start = column
            previous = column
        runs.append((int(row), start, previous + 1))
    return runs


def profile(image, config, pad=None, region=None):
    forward, cross = basis(config)
    cx = config["cx"] + forward[0] * config["station"]
    cy = config["cy"] + forward[1] * config["station"]
    offsets = np.arange(-config["half_span"], config["half_span"] + 1)
    xs = np.rint(cx + offsets*cross[0]).astype(int)
    ys = np.rint(cy + offsets*cross[1]).astype(int)
    h, w = image.shape[:2]
    valid = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    levels = image[ys[valid], xs[valid]] @ LUMA
    x0 = region["x0"] if region else 0
    y0 = region["y0"] if region else 0
    on_pad = np.zeros(len(offsets), dtype=bool)
    if pad is not None:
        local_x = xs - x0
        local_y = ys - y0
        within = (local_x >= 0) & (local_x < pad.shape[1]) & (local_y >= 0) & (local_y < pad.shape[0])
        on_pad[within] = pad[local_y[within], local_x[within]] if np.any(within) else False
    values = []
    for offset, x, y, level, marked_pad in zip(offsets[valid], xs[valid], ys[valid], levels,
                                               on_pad[valid]):
        values.append({"offset": int(offset), "x": int(x), "y": int(y),
                       "brightness": float(level), "marked": bool(level <= config["cutoff"]),
                       "pad": bool(marked_pad)})
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


def pad_segments(values, config):
    """Every run on the transect that belongs to the accepted palm pad, in order."""
    segments = []
    current = []
    for sample in values:
        if not sample["pad"]:
            if current:
                segments.append(current)
                current = []
            continue
        if current and sample["offset"] != current[-1]["offset"] + 1:
            segments.append(current)
            current = []
        current.append(sample)
    if current:
        segments.append(current)
    result = []
    for index, run in enumerate(segments):
        pixels = run[-1]["offset"] - run[0]["offset"] + 1
        result.append({"index": index, "pixels": pixels,
                       "width_mm": pixels * config["mm_per_px"],
                       "start": run[0]["offset"], "end": run[-1]["offset"],
                       "ends": [run[0], run[-1]]})
    return result


def report(image, config):
    pad, area = pad_analysis(image, config)
    values, center, across = profile(image, config, pad, area["region"])
    mode = config.get("reading_mode", "pad")
    reading = None
    if mode == "pad":
        segments = pad_segments(values, config)
        pixels = sum(segment["pixels"] for segment in segments)
        reading = {"mode": "pad", "pixels": pixels,
                   "width_mm": pixels * config["mm_per_px"], "segments": segments}
    else:
        span = longest_mark(values)
        if span:
            width = span[-1]["offset"] - span[0]["offset"] + 1
            reading = {"mode": "continuous", "pixels": width,
                       "width_mm": width*config["mm_per_px"], "ends": [span[0], span[-1]],
                       "segments": [{"index": 0, "pixels": width,
                                     "width_mm": width*config["mm_per_px"],
                                     "start": span[0]["offset"], "end": span[-1]["offset"],
                                     "ends": [span[0], span[-1]]}]}
    return {"reading": reading, "area": area, "profile": values, "center": center,
            "across": across, "angle": config["angle"], "station": config["station"]}


def practice():
    w, h = 746, 486
    cfg = settings({"cx": 374, "cy": 287, "angle": 270, "station": 0,
                    "half_span": 118, "cutoff": 155, "mm_per_px": 0.8,
                    "reading_mode": "pad"}, (w, h))
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
    # The teaching palm is split by two light press seams and holds one interior white pore.
    palm = (xx > 296) & (xx < 452) & (yy > 232) & (yy < 340)
    shaping[palm & (((xx >= 337) & (xx <= 339)) | ((xx >= 409) & (xx <= 411)))] = 0
    pore = ((xx - 388)/6)**2 + ((yy - 268)/5)**2 <= 1
    shaping[pore] = 0
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
