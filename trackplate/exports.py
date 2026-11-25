"""Export the observed cross-section with the original image and coordinates."""

from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import compare
from .transect import basis, png


def trace_table(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("offset_px", "x_px", "y_px", "luma", "marked"))
    for point in result["profile"]:
        writer.writerow((point["offset"], point["x"], point["y"], f'{point["brightness"]:.3f}', int(point["marked"])))
    return buffer.getvalue().encode()


def proof(image, card, result):
    height, width = image.shape[:2]
    output = Image.new("RGB", (max(412,width),height+65), "#352a21")
    output.paste(Image.fromarray(image), (0,0))
    draw = ImageDraw.Draw(output)
    cfg = card["settings"]
    cx, cy = cfg["cx"], cfg["cy"]
    forward, _ = basis(cfg)
    draw.line((cx, cy, cx+forward[0]*cfg["half_span"], cy+forward[1]*cfg["half_span"]), fill="#4a6965", width=2)
    if values := result["profile"]:
        draw.line((values[0]["x"], values[0]["y"], values[-1]["x"], values[-1]["y"]), fill="#b46f35", width=2)
    result_read = result["reading"]
    if result_read:
        for p in result_read["ends"]:
            draw.ellipse((p["x"]-4,p["y"]-4,p["x"]+4,p["y"]+4), fill="#2f584c")
        detail = f'Dark span {result_read["pixels"]} px  /  {result_read["width_mm"]:.2f} mm'
    else:
        detail = "No marked span in the visible transect"
    draw.text((14,height+14), f'Transverse station {result["station"]} px  Axis {result["angle"]:.1f} deg', fill="#f6ead8")
    draw.text((14,height+35), detail, fill="#e0b482")
    return png(output)


def package(card, original, image, result):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        output.writestr("print.png", original)
        output.writestr("reading.json", json.dumps({"card": card, "reading": result["reading"],
                                                    "center": result["center"]}, ensure_ascii=False, indent=2))
        output.writestr("transect-profile.csv", trace_table(result))
        output.writestr("track-proof.png", proof(image, card, result))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Two-card millimetre comparison exports
# ---------------------------------------------------------------------------

# Saturated, stable palette.  The top-down map is rendered with nearest-neighbour
# scaling, so these exact RGB values survive into the PNG.
COMPARE_COLORS = {
    compare.NEW: (46, 160, 67),      # 新印 added mark: green
    compare.GONE: (224, 49, 49),     # 消失 lost mark: red
    compare.BOTH: (47, 75, 199),     # 都在: indigo
    compare.BLANK: (198, 205, 214),  # 都空: pale grey
    compare.OUTSIDE: (232, 226, 214),  # 缺拍: warm paper grey
}
COMPARE_ALPHA = {
    compare.NEW: 118, compare.GONE: 118, compare.BOTH: 96,
    compare.BLANK: 70, compare.OUTSIDE: 46,
}
_ENGLISH = {compare.NEW: "NEW", compare.GONE: "GONE", compare.BOTH: "BOTH",
            compare.BLANK: "BLANK", compare.OUTSIDE: "ONE SIDE MISSED"}

_CJK_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf",
)


def font(size):
    for candidate in _CJK_FONT_CANDIDATES:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _use_chinese_font():
    return any(Path(candidate).is_file() for candidate in _CJK_FONT_CANDIDATES)


def compare_table(report):
    """Flat CSV: every row carries the two REAL source coordinates."""
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow((
        "iu", "iv", "u_mm_forward", "v_mm_lateral", "category", "category_zh",
        "judged", "current_x", "current_y", "current_inside",
        "current_brightness", "current_marked",
        "old_x", "old_y", "old_inside", "old_brightness", "old_marked",
    ))
    for cell in report["cells"]:
        cur, old_side = cell["current"], cell["old"]
        writer.writerow((
            cell["iu"], cell["iv"], f'{cell["u"]:.6f}', f'{cell["v"]:.6f}',
            cell["category"], cell["category_zh"], int(cell["judged"]),
            cur["x"], cur["y"], int(cur["inside"]),
            "" if cur["brightness"] is None else f'{cur["brightness"]:.3f}',
            "" if cur["marked"] is None else int(cur["marked"]),
            old_side["x"], old_side["y"], int(old_side["inside"]),
            "" if old_side["brightness"] is None else f'{old_side["brightness"]:.3f}',
            "" if old_side["marked"] is None else int(old_side["marked"]),
        ))
    return buffer.getvalue().encode()


def _category_index_grid(report):
    """Array indexed [iu + span, iv + span] -> palette index (-1 if absent)."""
    geo = report["frame"]
    span = int(geo["index_span"])
    side = 2 * span + 1
    codes = np.full((side, side), -1, dtype=np.int16)
    index = {code: i for i, code in enumerate(compare.CATEGORY_ORDER)}
    for cell in report["cells"]:
        codes[cell["iu"] + span, cell["iv"] + span] = index[cell["category"]]
    return codes


def _source_overlay(image, card, report):
    """Rasterize change categories into this card's source-pixel frame."""
    settings = card["settings"]
    geo = report["frame"]
    span = int(geo["index_span"])
    step = float(geo["step_mm"])
    scale = float(settings["mm_per_px"])
    rad = math.radians(float(settings["angle"]))
    fx, fy = math.cos(rad), math.sin(rad)
    qx, qy = -fy, fx

    height, width = image.shape[:2]
    yy, xx = np.indices((height, width), dtype=np.float64)
    dx = xx - float(settings["cx"])
    dy = yy - float(settings["cy"])
    u_mm = (dx * fx + dy * fy) * scale
    v_mm = (dx * qx + dy * qy) * scale
    iu = np.floor(u_mm / step + 0.5).astype(np.int64)
    iv = np.floor(v_mm / step + 0.5).astype(np.int64)

    grid = _category_index_grid(report)
    side = grid.shape[0]
    in_index = (iu >= -span) & (iu <= span) & (iv >= -span) & (iv <= span)
    pick = np.full((height, width), -1, dtype=np.int16)
    if in_index.any():
        pick[in_index] = grid[iu[in_index] + span, iv[in_index] + span]

    palette = np.array([COMPARE_COLORS[code] for code in compare.CATEGORY_ORDER], dtype=np.uint8)
    alphas = np.array([COMPARE_ALPHA[code] for code in compare.CATEGORY_ORDER], dtype=np.uint8)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    painted = pick >= 0
    rgba[painted, :3] = palette[pick[painted]]
    rgba[painted, 3] = alphas[pick[painted]]
    return Image.fromarray(rgba, "RGBA")


def _fit_box(image_size, box):
    box_x, box_y, box_w, box_h = box
    src_w, src_h = image_size
    scale = min(box_w / src_w, box_h / src_h)
    draw_w = max(1, int(round(src_w * scale)))
    draw_h = max(1, int(round(src_h * scale)))
    x = box_x + (box_w - draw_w) // 2
    y = box_y + (box_h - draw_h) // 2
    return x, y, draw_w, draw_h, scale


def _draw_source_panel(output, image, card, report, box, title, zh):
    box_x, box_y, box_w, box_h = box
    draw = ImageDraw.Draw(output)
    title_font = font(17)
    small = font(13)
    draw.text((box_x + 2, box_y - 24), title, fill="#f6ead8", font=title_font)

    rgb = Image.fromarray(image, "RGB")
    px, py, draw_w, draw_h, scale = _fit_box(rgb.size, box)
    thumb = rgb.resize((draw_w, draw_h), Image.LANCZOS)
    output.paste(thumb, (px, py))

    overlay = _source_overlay(image, card, report)
    overlay = overlay.resize((draw_w, draw_h), Image.NEAREST)
    output.alpha_composite(overlay, (px, py))

    # Mapped boundary of the shared square: the true calibrated common range.
    corners = compare.frame(card)
    polygon = [(px + x * scale, py + y * scale) for x, y in corners]
    draw = ImageDraw.Draw(output)
    draw.polygon(polygon, outline=(255, 214, 0))
    line_width = 3
    draw.line(polygon + [polygon[0]], fill=(255, 214, 0), width=line_width, joint="curve")
    draw.rectangle((px, py, px + draw_w, py + draw_h), outline=(120, 104, 88), width=1)
    settings = card["settings"]
    detail = (f"origin ({settings['cx']:g}, {settings['cy']:g}) px   "
              f"scale {settings['mm_per_px']:g} mm/px   cutoff {settings['cutoff']}")
    draw.text((box_x + 2, box_y + box_h + 8), detail, fill="#ddc8b7", font=small)


def _topdown_map(report, size=420):
    geo = report["frame"]
    side = int(geo["side"])
    grid = _category_index_grid(report)
    palette = np.array([COMPARE_COLORS[code] for code in compare.CATEGORY_ORDER], dtype=np.uint8)
    # Grid rows run iu = -span .. +span; flip so +u (forward) is at the top.
    tex = Image.fromarray(palette[np.maximum(grid[::-1], 0)].astype("uint8"), "RGB")
    tex = tex.resize((size, size), Image.NEAREST)
    pad_l, pad_t, pad_b = 44, 30, 28
    canvas = Image.new("RGB", (size + pad_l + 24, size + pad_t + pad_b), "#fdfaf6")
    canvas.paste(tex, (pad_l, pad_t))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((pad_l, pad_t, pad_l + size, pad_t + size), outline=(69, 53, 42), width=2)
    label_font = font(15)
    if _use_chinese_font():
        top, bottom, left, right = "前", "后", "左", "右"
    else:
        top, bottom, left, right = "FWD", "BACK", "L", "R"
    bbox = draw.textbbox((0, 0), top, font=label_font)
    tw = bbox[2] - bbox[0]
    draw.text((pad_l + size / 2 - tw / 2, 7), top, fill="#6b594b", font=label_font)
    bbox = draw.textbbox((0, 0), bottom, font=label_font)
    tw = bbox[2] - bbox[0]
    draw.text((pad_l + size / 2 - tw / 2, pad_t + size + 6), bottom,
              fill="#6b594b", font=label_font)
    draw.text((6, pad_t + size / 2 - 8), left, fill="#6b594b", font=label_font)
    draw.text((pad_l + size + 8, pad_t + size / 2 - 8), right, fill="#6b594b", font=label_font)
    return canvas


def compare_proof(current_image, current_card, old_image, old_card, report):
    zh = _use_chinese_font()

    width = 1280
    panel_w, panel_h = 588, 430
    left_x, right_x = 28, 664
    panel_y = 176
    map_size = 420
    height = panel_y + panel_h + 46 + map_size + 92

    output = Image.new("RGBA", (width, height), (53, 42, 33, 255))
    draw = ImageDraw.Draw(output)
    title_font = font(24)
    body_font = font(15)
    big_font = font(20)

    if zh:
        title = "新旧对照 · 同一毫米位置的变化校样"
    else:
        title = "TrackPlate · before/after comparison proof"
    draw.text((28, 20), title, fill=(246, 234, 216), font=title_font)

    counts = report["counts"]
    geo = report["frame"]
    if zh:
        subtitle = (f"共同半宽 {geo['half_width_mm']:.2f} mm（取两卡较小实际取样半宽）   "
                     f"格距 {geo['step_mm']:.3f} mm   格网 {geo['side']}×{geo['side']}   "
                     f"可判 {counts['judged']}   缺拍 {counts['outside']}")
    else:
        subtitle = (f"common half-width {geo['half_width_mm']:.2f} mm (min of both cards)   "
                    f"grid {geo['step_mm']:.3f} mm   {geo['side']}x{geo['side']}   "
                    f"judged {counts['judged']}   missed {counts['outside']}")
    draw.text((28, 56), subtitle, fill=(224, 180, 130), font=body_font)
    draw.text((28, 78), f"now: {current_card['name']} [{current_card['id'][:8]}]   vs   "
                        f"old: {old_card['name']} [{old_card['id'][:8]}]",
              fill=(221, 200, 183), font=body_font)

    # Legend chips.
    chip_x = 28
    chip_y = 112
    for code in compare.CATEGORY_ORDER:
        draw.rectangle((chip_x, chip_y, chip_x + 20, chip_y + 20), fill=COMPARE_COLORS[code])
        label = compare.CATEGORY_ZH[code] if zh else _ENGLISH[code]
        caption = f"{label} {counts[code]}"
        draw.text((chip_x + 27, chip_y + 2), caption, fill=(246, 234, 216), font=body_font)
        chip_x += 34 + int(draw.textlength(caption, font=body_font)) + 26

    _draw_source_panel(output, current_image, current_card, report,
                       (left_x, panel_y, panel_w, panel_h),
                       ("现在这张（修整后）" if zh else "CURRENT CARD (real source pixels)"), zh)
    _draw_source_panel(output, old_image, old_card, report,
                       (right_x, panel_y, panel_w, panel_h),
                       ("旧照（修整前）" if zh else "OLD CARD (real source pixels)"), zh)

    map_image = _topdown_map(report, map_size).convert("RGBA")
    map_y = panel_y + panel_h + 46
    output.alpha_composite(map_image, (28, map_y))

    info_x = 520
    info_y = map_y + 6
    if zh:
        lines = [
            ("共同拍到的原点附近方形区域", big_font, (246, 234, 216)),
            (f"前后、左右均为 ±{geo['half_width_mm']:.2f} mm", body_font, (221, 200, 183)),
            ("两卡各自经原点/前向/标尺映射到同一毫米位置。", body_font, (221, 200, 183)),
            ("每张照片使用自己的印痕亮度上限判定。", body_font, (221, 200, 183)),
            ("只有两侧都落在实际像素内才分类；一侧缺拍标灰不判。", body_font, (221, 200, 183)),
            (f"新印 {counts['new']} · 消失 {counts['gone']} · "
             f"都在 {counts['both']} · 都空 {counts['blank']}", body_font, (246, 234, 216)),
        ]
    else:
        lines = [
            ("Shared near-origin square, top-down in mm", big_font, (246, 234, 216)),
            (f"forward/back and left/right: +/- {geo['half_width_mm']:.2f} mm",
             body_font, (221, 200, 183)),
            ("Each card maps mm positions with its own origin, axis and scale.",
             body_font, (221, 200, 183)),
            ("Each side is thresholded with its own impression cutoff.",
             body_font, (221, 200, 183)),
            ("Classified only where BOTH sides hit real pixels; grey = missed side.",
             body_font, (221, 200, 183)),
            (f"NEW {counts['new']}  /  GONE {counts['gone']}  /  "
             f"BOTH {counts['both']}  /  BLANK {counts['blank']}",
             body_font, (246, 234, 216)),
        ]
    y = info_y
    for text_line, text_font, color in lines:
        draw.text((info_x, y), text_line, fill=color, font=text_font)
        y += 24 if text_font is body_font else 34

    buffer = BytesIO()
    output.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


# Naming aliases for the comparison proof/table exporters.
comparison_proof = compare_proof
comparison_table = compare_table
diff_proof = compare_proof
change_proof = compare_proof

compare_proof_png = compare_proof
comparison_proof_png = compare_proof
diff_proof_png = compare_proof
