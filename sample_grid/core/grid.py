"""Fixed-lattice grid construction (GRID-05 / Pitfall 7) + universal-AR detect.

``build_grid`` derives the full axis domains up front, then pins a ``Cell`` for
*every* (row, col) coordinate via the cartesian product — the lattice never
collapses or shifts, even when samples are absent. A coordinate with no sample
is ``MISSING``; a present-but-undecodable file is ``BROKEN`` (Pillow
``verify()``, D-10); otherwise ``POPULATED`` with a per-cell ``ar_mismatch``
flag set when its aspect ratio differs from the detected universal AR (D-11).
"""
from __future__ import annotations

import re
from collections import Counter
from math import gcd
from pathlib import Path

from PIL import Image

from sample_grid.core.model import (
    Cell,
    CellState,
    GridConfig,
    GridModel,
    SampleIndex,
)


_DIGITS = re.compile(r"(\d+)")


def natural_key(value):
    """Numeric-aware ("natural") sort key (GRID-06 / D-11).

    ``step_200`` < ``step_1000`` < ``step_30000`` even when unpadded: embedded
    digit runs compare as integers, not lexically. Pure-int values sort ahead of
    any non-numeric label (numeric-first tier preserved from the old ``_as_number``
    stub), and the same key orders a numeric seed axis correctly too.
    """
    s = str(value)
    # A whole-value pure number gets the strongest (numeric-first) ordering tier.
    # NOTE: the tier flag must NOT carry the full string alongside it — doing so
    # (as an earlier sketch did) makes the outer tuple compare lexically and
    # defeats the natural key ("step_1000" would sort before "step_200"). The
    # natural `key` tuple alone must break ties within the label tier.
    try:
        return (0, int(s))
    except (TypeError, ValueError):
        pass
    parts = _DIGITS.split(s)  # e.g. "step_200" -> ["step_", "200", ""]
    key = tuple(
        (1, p.lower()) if i % 2 == 0 else (0, int(p))
        for i, p in enumerate(parts)
        if p != ""
    )
    return (1, key)


def _ar_of(path: Path) -> "tuple[int, int] | None":
    """Reduced (w, h) aspect ratio of an image, or None if it can't be read."""
    try:
        with Image.open(path) as im:
            w, h = im.size
        g = gcd(w, h) or 1
        return (w // g, h // g)
    except Exception:
        return None


def detect_universal_ar(index: SampleIndex) -> "tuple[int, int]":
    """Dominant aspect ratio across the index (D-11); (1, 1) when none readable."""
    ars = Counter(a for s in index if (a := _ar_of(s.path)))
    return ars.most_common(1)[0][0] if ars else (1, 1)


def is_decodable(path: Path) -> bool:
    """True if Pillow can decode the file; False (no raise) if it can't (D-10).

    Opens a *fresh* image inside the try and calls ``verify()`` — which validates
    the file structure without loading full pixel data. Per Pillow's contract the
    image object must not be reused after ``verify()``, so it is opened anew each
    call and never returned. Any exception (missing file, truncated/corrupt bytes,
    unidentified format) → ``False`` → the coordinate classifies as ``BROKEN``.
    """
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def build_grid(index: SampleIndex, config: GridConfig) -> GridModel:
    """Build a dense Steps x Prompts lattice from a SampleIndex (Pattern 2)."""
    by_coord = {(s.dims[config.rows], s.dims[config.cols]): s for s in index}

    row_values = sorted({s.dims[config.rows] for s in index}, key=natural_key)
    col_values = sorted({s.dims[config.cols] for s in index}, key=natural_key)
    cell_ar = detect_universal_ar(index)

    cells: list[list[Cell]] = []
    for row in row_values:
        row_cells: list[Cell] = []
        for col in col_values:
            sample = by_coord.get((row, col))
            if sample is None:
                # Absent coordinate — never skipped (Pitfall 1). (D-09)
                row_cells.append(Cell(CellState.MISSING))
            elif not is_decodable(sample.path):
                # File present but won't decode → BROKEN, sample retained. (D-10)
                row_cells.append(Cell(CellState.BROKEN, sample=sample))
            else:
                # Populated; flag a stray aspect ratio for letterbox fallback. (D-11)
                row_cells.append(
                    Cell(
                        CellState.POPULATED,
                        sample=sample,
                        ar_mismatch=_ar_of(sample.path) != cell_ar,
                    )
                )
        cells.append(row_cells)

    return GridModel(
        row_values=row_values,
        col_values=col_values,
        cells=cells,
        cell_ar=cell_ar,
    )
