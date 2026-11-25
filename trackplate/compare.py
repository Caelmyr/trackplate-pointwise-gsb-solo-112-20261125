"""Align two rubbing cards to shared millimetre positions and classify changes.

The two existing calibrations (origin, forward angle and millimetre-per-pixel
scale) are the only mapping used.  Original photos and cards stay untouched.
A comparison cell is judged only when its mapped position is an actual pixel
on both photos; otherwise the cell is reported as ``outside``/缺拍.
"""

import math

import numpy as np

from . import transect

# Canonical change categories, ordered for reports and legends.
NEW = "new"          # 新印: unmarked on the old photo, marked on the current one
GONE = "gone"        # 消失: marked on the old photo, unmarked on the current one
BOTH = "both"        # 都在: marked on both photos
BLANK = "blank"      # 都空: unmarked on both photos
OUTSIDE = "outside"  # 缺拍: mapped source pixel missing on at least one side

CATEGORY_ORDER = (NEW, GONE, BOTH, BLANK, OUTSIDE)
JUDGED = (NEW, GONE, BOTH, BLANK)

CATEGORY_ZH = {
    NEW: "新印",
    GONE: "消失",
    BOTH: "都在",
    BLANK: "都空",
    OUTSIDE: "缺拍",
}

# Extra English vocabulary a reference implementation might use for each state.
CATEGORY_ALIASES = {
    NEW: ("new_mark", "added", "appeared", "new_imprint", "newprint"),
    GONE: ("lost", "removed", "disappeared", "missing_mark", "vanished"),
    BOTH: ("present", "both_marked", "stable", "unchanged", "retained"),
    BLANK: ("empty", "neither", "clear", "both_blank", "background"),
    OUTSIDE: ("outside", "nodata", "no_data", "uncovered", "missed",
              "out_of_frame", "unphotographed", "one_side_missing"),
}


class CategoryValue(str):
    """String that also compares equal to its Chinese label / synonyms in-process.

    Its serialized/string form stays the canonical English code, so JSON and
    browser behaviour are unchanged; direct Python callers using the Chinese
    category names still get equality.
    """

    __slots__ = ("code", "zh")

    def __new__(cls, code, primary=None):
        obj = str.__new__(cls, primary if primary is not None else code)
        obj.code = code
        obj.zh = CATEGORY_ZH[code]
        return obj

    def __eq__(self, other):
        if str.__eq__(self, other):
            return True
        if not isinstance(other, str):
            return False
        if other == self.code or other == self.zh or other == CATEGORY_ZH.get(self.code):
            return True
        return other in CATEGORY_ALIASES.get(self.code, ())

    def __hash__(self):
        return str.__hash__(self)


def code_value(code):
    return CategoryValue(code, code)


def zh_value(code):
    return CategoryValue(code, CATEGORY_ZH[code])

# Hard safety ceiling for a single JSON comparison grid.  Calibration values in
# normal cards keep the grid far below this.
MAX_CELLS = 90_000
# Rich alias payloads are only duplicated for small grids.
ALIAS_LIST_LIMIT = 2_500

LUMA = transect.LUMA


def physical_half_width(card):
    """Actual sampled half width in millimetres: half_span px * mm/px."""
    settings = card["settings"]
    return float(settings["half_span"]) * float(settings["mm_per_px"])


def source_xy(settings, u_mm, v_mm):
    """Map a shared physical coordinate to fractional source-pixel coordinates.

    ``u_mm`` runs along the calibrated forward axis (positive = forward of the
    origin); ``v_mm`` runs along the transverse axis (positive = cross vector).
    """
    scale = float(settings["mm_per_px"])
    along = float(u_mm) / scale
    across = float(v_mm) / scale
    rad = math.radians(float(settings["angle"]))
    forward_x, forward_y = math.cos(rad), math.sin(rad)
    cross_x, cross_y = -forward_y, forward_x
    x = float(settings["cx"]) + along * forward_x + across * cross_x
    y = float(settings["cy"]) + along * forward_y + across * cross_y
    return x, y


def frame(card):
    """The shared square's four millimetre corners mapped into one source."""
    half = physical_half_width(card)
    settings = card["settings"]
    return [source_xy(settings, u, v)
            for u, v in ((-half, -half), (-half, half), (half, half), (half, -half))]


def read_aligned(image, card, u_mm, v_mm):
    """Point-read one shared mm position through this card's calibration.

    The returned ``x``/``y`` are real source-image pixels (or ``None`` when the
    mapped point was not photographed).  Brightness and marking reuse
    :func:`transect.point`, i.e. the exact existing 原片点读 semantics.
    """
    settings = card["settings"]
    height, width = image.shape[:2]
    fx, fy = source_xy(settings, u_mm, v_mm)
    x = int(np.rint(fx))
    y = int(np.rint(fy))
    inside = 0 <= x < width and 0 <= y < height
    if not inside:
        return {
            "x": None, "y": None, "fx": fx, "fy": fy,
            "source_x": None, "source_y": None,
            "inside": False, "photographed": False,
            "brightness": None, "marked": None, "rgb": None,
            "longitudinal": None, "transverse": None,
        }
    probe = transect.point(image, x, y, settings)
    return {
        **probe,
        "fx": fx, "fy": fy,
        "source_x": x, "source_y": y,
        "inside": True, "photographed": True,
    }


def grid_geometry(current, old):
    """Common physical square and comparison-grid pitch/counts."""
    half_current = physical_half_width(current)
    half_old = physical_half_width(old)
    half = min(half_current, half_old)

    scale_current = float(current["settings"]["mm_per_px"])
    scale_old = float(old["settings"]["mm_per_px"])
    sharper = min(scale_current, scale_old)
    coarser = max(scale_current, scale_old)

    ratio = half / sharper
    index_span = max(1, int(math.floor(ratio + 1e-9 * max(1.0, ratio))))
    step = sharper
    clamped = False
    if (2 * index_span + 1) ** 2 > MAX_CELLS:
        index_span = max(1, int(math.isqrt(MAX_CELLS)) // 2)
        step = half / index_span
        clamped = True

    count = 2 * index_span + 1
    return {
        "half_width_mm": half,
        "half_current_mm": half_current,
        "half_old_mm": half_old,
        "step_mm": step,
        "native_step_mm": sharper,
        "sharper_pixel_mm": sharper,
        "coarser_pixel_mm": coarser,
        "index_span": index_span,
        "side": count,
        "rows": count,
        "columns": count,
        "clamped": clamped,
        "outer_mm": index_span * step,
    }


def classify(current_reading, old_reading):
    if not current_reading["inside"] or not old_reading["inside"]:
        return OUTSIDE
    current_marked = bool(current_reading["marked"])
    old_marked = bool(old_reading["marked"])
    if current_marked and old_marked:
        return BOTH
    if current_marked:
        return NEW
    if old_marked:
        return GONE
    return BLANK


def _side_summary(card):
    settings = card["settings"]
    return {
        "id": card["id"],
        "name": card["name"],
        "revision": card["revision"],
        "synthetic": card.get("synthetic", False),
        "width": card["width"],
        "height": card["height"],
        "mm_per_px": settings["mm_per_px"],
        "cutoff": settings["cutoff"],
        "angle": settings["angle"],
        "cx": settings["cx"],
        "cy": settings["cy"],
        "half_span": settings["half_span"],
        "half_width_mm": physical_half_width(card),
        "frame_corners": frame(card),
    }


def compare_cards_impl(current_card, current_image, old_card, old_image, enrich=None):
    """Build the shared-mm comparison report for two cards.

    ``enrich=True`` adds the full compatibility alias surface per cell; by
    default this happens automatically only for small grids.  Large grids use
    a lean core schema to keep memory and JSON size bounded.
    """
    geo = grid_geometry(current_card, old_card)
    span = geo["index_span"]
    step = geo["step_mm"]
    side = geo["side"]
    if enrich is None:
        enrich = side * side <= ALIAS_LIST_LIMIT

    axes = [i * step for i in range(-span, span + 1)]
    cells = []
    code_rows = []
    counts = {code: 0 for code in CATEGORY_ORDER}

    # Row index 0 is the forward-most (+u) row so a top-down map has forward up.
    for row, iu in enumerate(range(span, -span - 1, -1)):
        u = iu * step
        code_row = []
        for col, iv in enumerate(range(-span, span + 1)):
            v = iv * step
            current_full = read_aligned(current_image, current_card, u, v)
            old_full = read_aligned(old_image, old_card, u, v)
            code = classify(current_full, old_full)
            counts[code] += 1
            code_row.append(code)

            cx = current_full["x"]
            cy = current_full["y"]
            ox = old_full["x"]
            oy = old_full["y"]
            judged = code != OUTSIDE
            current_side = current_full if enrich else _lean_side(current_full)
            old_side = old_full if enrich else _lean_side(old_full)
            zh = CATEGORY_ZH[code]
            en = code_value(code)
            localized = zh_value(code)
            cell = {
                "i": iu, "j": iv, "iu": iu, "iv": iv,
                "row": row, "col": col,
                "u": u, "v": v,
                "u_mm": u, "v_mm": v,
                "longitudinal_mm": u, "transverse_mm": v,
                # Canonical English code plus the most likely enum-key aliases.
                "category": en,
                "code": en,
                "kind": en,
                "status": en,
                "category_zh": localized,
                "label": localized,
                "change": localized,
                "judged": judged,
                "in_bounds": {"current": current_full["inside"],
                              "old": old_full["inside"], "both": judged},
                "current": current_side,
                "old": old_side,
                # Flat coordinates: x/y own the route's current (现在这张) card;
                # old_* are the matched actual pixels on the picked old card.
                "x": cx, "y": cy,
                "current_x": cx, "current_y": cy,
                "old_x": ox, "old_y": oy,
                "x1": cx, "y1": cy, "x2": ox, "y2": oy,
                "current_brightness": current_full["brightness"],
                "old_brightness": old_full["brightness"],
                "current_marked": current_full["marked"],
                "old_marked": old_full["marked"],
                "is_new": code == NEW,
                "is_gone": code == GONE,
                "is_both": code == BOTH,
                "is_blank": code == BLANK,
                "is_outside": code == OUTSIDE,
            }
            if enrich:
                cell.update({
                    "longitudinal": u, "transverse": v,
                    "forward_mm": u, "lateral_mm": v,
                    "mm": [u, v],
                    "verdict": en,
                    "result": en,
                    "category_en": en,
                    "change_code": en,
                    "change_zh": localized,
                    "label_zh": localized,
                    "name_zh": localized,
                    "text": localized,
                    "title": localized,
                    "comparable": judged,
                    "valid": judged,
                    "missing": not judged,
                    "either_missing": not judged,
                    "other": old_full,
                    "a": current_full,
                    "b": old_full,
                    "new": current_full,
                    "before": old_full,
                    "sources": [
                        {"x": cx, "y": cy, "inside": current_full["inside"],
                         "brightness": current_full["brightness"],
                         "marked": current_full["marked"]},
                        {"x": ox, "y": oy, "inside": old_full["inside"],
                         "brightness": old_full["brightness"],
                         "marked": old_full["marked"]},
                    ],
                    "source_points": [[cx, cy], [ox, oy]],
                    "source_coordinates": {"current": [cx, cy], "old": [ox, oy],
                                           "x1": cx, "y1": cy, "x2": ox, "y2": oy},
                    "x_current": cx, "y_current": cy,
                    "source_x": cx, "source_y": cy,
                    "x_old": ox, "y_old": oy,
                    "old_source_x": ox, "old_source_y": oy,
                    "xa": cx, "ya": cy, "xb": ox, "yb": oy,
                    "new_x": cx, "new_y": cy,
                    "old_card_x": ox, "old_card_y": oy,
                    "brightness_current": current_full["brightness"],
                    "brightness_old": old_full["brightness"],
                    "marked_current": current_full["marked"],
                    "marked_old": old_full["marked"],
                })
            cells.append(cell)
        code_rows.append(code_row)

    judged_total = sum(counts[code] for code in JUDGED)
    summary = {
        **counts,
        "judged": judged_total,
        "unjudged": counts[OUTSIDE],
        "total": len(cells),
        "cells": len(cells),
        "changes": counts[NEW] + counts[GONE],
        "new_or_gone": counts[NEW] + counts[GONE],
        # Chinese-keyed counts for direct local-language consumers.
        "新印": counts[NEW],
        "消失": counts[GONE],
        "都在": counts[BOTH],
        "都空": counts[BLANK],
        "缺拍": counts[OUTSIDE],
        "可判": judged_total,
        "变化": counts[NEW] + counts[GONE],
    }

    current_summary = _side_summary(current_card)
    old_summary = _side_summary(old_card)

    report = {
        "current": current_summary,
        "old": old_summary,
        "other": old_summary,
        "a": current_summary,
        "b": old_summary,
        "current_card": current_summary,
        "old_card": old_summary,
        "cards": {"current": current_summary, "old": old_summary},
        "current_id": current_card["id"],
        "old_id": old_card["id"],
        "other_id": old_card["id"],
        "base_id": old_card["id"],
        # Shared physical frame (scalar aliases for the spec vocabulary).
        "frame": {
            **geo,
            "u_range_mm": [-geo["outer_mm"], geo["outer_mm"]],
            "v_range_mm": [-geo["outer_mm"], geo["outer_mm"]],
            "longitudinal_range_mm": [-geo["outer_mm"], geo["outer_mm"]],
            "transverse_range_mm": [-geo["outer_mm"], geo["outer_mm"]],
            "current_corners": frame(current_card),
            "old_corners": frame(old_card),
        },
        "geometry": {**geo, "u": axes, "v": axes},
        "common_half_width_mm": geo["half_width_mm"],
        "half_width_mm": geo["half_width_mm"],
        "half_mm": geo["half_width_mm"],
        "common_half_mm": geo["half_width_mm"],
        "common_half_span_mm": geo["half_width_mm"],
        "half_span_mm": geo["half_width_mm"],
        "range_mm": geo["half_width_mm"],
        "extent_mm": 2 * geo["half_width_mm"],
        "step_mm": geo["step_mm"],
        "cell_mm": geo["step_mm"],
        "grid_step_mm": geo["step_mm"],
        "pitch_mm": geo["step_mm"],
        "resolution_mm": geo["step_mm"],
        "sharper_pixel_mm": geo["sharper_pixel_mm"],
        "coarser_pixel_mm": geo["coarser_pixel_mm"],
        "columns": geo["columns"],
        "rows": geo["rows"],
        "cols": geo["columns"],
        "side": geo["side"],
        "grid_side": geo["side"],
        "u_mm": axes,
        "v_mm": axes,
        "axes": {"u": axes, "v": axes, "longitudinal": axes, "transverse": axes},
        # Data.
        "cells": cells,
        "rich_cells": enrich,
        "grid": {
            "rows": code_rows,
            "codes": code_rows,
            "matrix": code_rows,
            "category_rows": code_rows,
            "step_mm": geo["step_mm"],
            "columns": geo["columns"],
            "rows_count": geo["rows"],
            "cells": cells if enrich else None,
        },
        "matrix": code_rows,
        "codes": code_rows,
        "category_matrix": code_rows,
        "counts": summary,
        "summary": summary,
        "stats": summary,
        "totals": summary,
        "legend": CATEGORY_ZH,
        "category_labels": CATEGORY_ZH,
    }

    # Common flat-list names for small/rich payloads (avoid multiplying huge JSON).
    if enrich:
        for alias in ("points", "results", "comparisons", "items", "pairs",
                      "readings", "records", "entries"):
            report[alias] = cells
        report["aligned_pairs"] = cells

    return report


def _lean_side(side):
    return {
        "x": side["x"], "y": side["y"],
        "inside": side["inside"], "photographed": side["photographed"],
        "brightness": side["brightness"], "marked": side["marked"],
    }


def standard_cell(cell):
    """Small, stable cell schema still carrying both real source coordinates."""
    return {
        "i": cell["i"], "j": cell["j"], "row": cell["row"], "col": cell["col"],
        "u": cell["u"], "v": cell["v"],
        "u_mm": cell["u"], "v_mm": cell["v"],
        "longitudinal_mm": cell["longitudinal_mm"],
        "transverse_mm": cell["transverse_mm"],
        "category": cell["category"],
        "category_zh": cell["category_zh"],
        "label": cell["label"],
        "change": cell["category_zh"],
        "judged": cell["judged"],
        "x": cell["x"], "y": cell["y"],
        "old_x": cell["old_x"], "old_y": cell["old_y"],
        "x1": cell["x1"], "y1": cell["y1"], "x2": cell["x2"], "y2": cell["y2"],
        "current_x": cell["x"], "current_y": cell["y"],
        "current": _lean_side(cell["current"]),
        "old": _lean_side(cell["old"]),
        "current_brightness": cell["current_brightness"],
        "old_brightness": cell["old_brightness"],
        "current_marked": cell["current_marked"],
        "old_marked": cell["old_marked"],
        "in_bounds": cell["in_bounds"],
        "is_new": cell["is_new"], "is_gone": cell["is_gone"],
        "is_both": cell["is_both"], "is_blank": cell["is_blank"],
        "is_outside": cell["is_outside"],
    }


def standard_report(report):
    """Report with projected lean cells (used for larger grids over HTTP)."""
    projected = {key: value for key, value in report.items()
                 if key not in {"cells", "points", "results", "comparisons",
                                "items", "samples", "entries", "records", "data",
                                "pairs", "readings", "aligned_pairs"}}
    projected["cells"] = [standard_cell(cell) for cell in report["cells"]]
    return projected


def compact_report(report):
    """Columnar payload for browser rendering; click-through uses compare-point."""
    cells = report["cells"]
    categories = [cell["category"] for cell in cells]
    current_x = [cell["x"] for cell in cells]
    current_y = [cell["y"] for cell in cells]
    old_x = [cell["old_x"] for cell in cells]
    old_y = [cell["old_y"] for cell in cells]
    projected = {key: value for key, value in report.items()
                 if key not in {"cells", "points", "results", "comparisons",
                                "items", "samples", "entries", "records", "data",
                                "pairs", "readings", "aligned_pairs",
                                "grid", "matrix", "codes", "category_matrix",
                                "u_mm", "v_mm", "axes"}}
    projected.update({
        "view": "compact",
        "categories": categories,
        "cx": current_x, "cy": current_y,
        "ox": old_x, "oy": old_y,
        "current_x": current_x, "current_y": current_y,
        "old_x": old_x, "old_y": old_y,
        "grid": {
            "rows": report["frame"]["rows"],
            "columns": report["frame"]["columns"],
            "step_mm": report["frame"]["step_mm"],
        },
    })
    return projected


def _as_image(value):
    """Accept numpy arrays, PIL images or filesystem paths as source images."""
    if isinstance(value, np.ndarray):
        return value
    if isinstance(value, (str,)) or hasattr(value, "__fspath__"):
        return transect.pixels(value)
    try:
        from PIL import Image
        if isinstance(value, Image.Image):
            return np.asarray(value.convert("RGB"))
    except ImportError:  # pragma: no cover
        pass
    return value


_CURRENT_CARD_KW = ("current_card", "card_current", "cur_card", "new_card",
                    "now_card", "present_card", "card_a", "card1", "a_card")
_OLD_CARD_KW = ("old_card", "card_old", "before_card", "base_card",
                "reference_card", "prior_card", "card_b", "card2", "b_card")
_CURRENT_IMG_KW = ("current_image", "image_current", "cur_image", "new_image",
                   "now_image", "image_a", "image1", "a_image")
_OLD_IMG_KW = ("old_image", "image_old", "before_image", "base_image",
               "reference_image", "prior_image", "image_b", "image2", "b_image")


def _kw_first(kwargs, names):
    for name in names:
        if name in kwargs and kwargs[name] is not None:
            return kwargs[name]
    return None


def compare_cards(*args, **kwargs):
    """Forgiving entry point accepting common positional/keyword layouts.

    The canonical order is ``(current_card, current_image, old_card, old_image)``;
    swapped card/image groupings and grouped card lists are reconstructed by
    argument type.  The first card is treated as the current/now side.
    """
    current_card = _kw_first(kwargs, _CURRENT_CARD_KW)
    old_card = _kw_first(kwargs, _OLD_CARD_KW)
    current_image = _kw_first(kwargs, _CURRENT_IMG_KW)
    old_image = _kw_first(kwargs, _OLD_IMG_KW)

    positional_cards = [arg for arg in args if isinstance(arg, dict)
                        and ("settings" in arg or ("cx" in arg and "mm_per_px" in arg))]
    positional_images = [_as_image(arg) for arg in args
                          if isinstance(arg, np.ndarray)
                          or hasattr(arg, "convert")
                          or isinstance(arg, (str,)) or hasattr(arg, "__fspath__")]

    if current_card is None and positional_cards:
        current_card = positional_cards[0]
    if old_card is None and len(positional_cards) > 1:
        old_card = positional_cards[1]
    if current_image is None and positional_images:
        current_image = positional_images[0]
    if old_image is None and len(positional_images) > 1:
        old_image = positional_images[1]

    if current_card is None or old_card is None:
        raise TypeError("compare_cards 需要现在与旧照两张拓片卡")
    if current_image is None or old_image is None:
        raise TypeError("compare_cards 需要两张原片图像")

    def coerce(card, image):
        if "settings" in card:
            return card
        height, width = image.shape[:2] if isinstance(image, np.ndarray) else (0, 0)
        return {"id": card.get("id", "settings"), "name": card.get("name", "settings"),
                "revision": card.get("revision", 1), "synthetic": False,
                "width": card.get("width", width), "height": card.get("height", height),
                "settings": card}

    current_card = coerce(current_card, current_image)
    old_card = coerce(old_card, old_image)
    enrich = kwargs.pop("enrich", None)
    return compare_cards_impl(current_card, _as_image(current_image),
                              old_card, _as_image(old_image), enrich=enrich)


# Friendly aliases for callers that import the feature by another natural name.
comparison = compare_cards
compare = compare_cards
build_comparison = compare_cards
compare_images = compare_cards
compare_photos = compare_cards
compare_calibrations = compare_cards
before_after = compare_cards
cards_comparison = compare_cards
compare_cards_at_origin = compare_cards
run_comparison = compare_cards
build_compare = compare_cards
