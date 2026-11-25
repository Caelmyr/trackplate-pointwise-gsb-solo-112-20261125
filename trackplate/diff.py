"""Short alias module for the card comparison/diff feature."""

from .compare import (  # noqa: F401
    CATEGORY_ZH,
    classify,
    compare,
    compare_cards,
    compare_cards_at_origin,
    compare_images,
    frame,
    grid_geometry,
    physical_half_width,
    read_aligned,
    source_xy,
    standard_report,
    compact_report,
)

diff_cards = compare_cards
card_diff = compare_cards
build_comparison_report = compare_cards
comparison_report = compare_cards
