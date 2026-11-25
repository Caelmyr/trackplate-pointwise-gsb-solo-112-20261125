"""Compatibility module name for the two-card comparison feature.

The implementation lives in :mod:`trackplate.compare`; this module re-exports
its public surface under the natural ``comparison`` name.
"""

from .compare import (  # noqa: F401
    BLANK,
    BOTH,
    CATEGORY_ORDER,
    CATEGORY_ZH,
    GONE,
    JUDGED,
    MAX_CELLS,
    NEW,
    OUTSIDE,
    classify,
    compact_report,
    compare,
    compare_cards,
    compare_cards_at_origin,
    compare_images,
    build_comparison,
    frame,
    grid_geometry,
    physical_half_width,
    read_aligned,
    source_xy,
    standard_cell,
    standard_report,
)

compare_report = compare_cards
build_report = compare_cards
run_comparison = compare_cards
