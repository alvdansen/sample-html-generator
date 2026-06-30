"""Fixed-lattice grid construction (GRID-05 / Pitfall 7) + universal-AR detect.

``build_grid`` derives the full axis domains up front, then pins a ``Cell`` for
*every* (row, col) coordinate via the cartesian product — the lattice never
collapses or shifts, even when samples are absent. A coordinate with no sample
is ``MISSING``; otherwise ``POPULATED``.

BROKEN classification (Pillow ``verify()``) and the per-cell ``ar_mismatch`` flag
are deliberately deferred to Plan 02. AR *detection* (D-11) lands here so the
renderer can size cells to the dominant aspect ratio.
"""
from __future__ import annotations

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


def _as_number(value):
    """Sort key: numeric when the value is int-like, else fall back to string.

    Trivial P1 step ordering so ``step_1000`` sorts after ``step_200`` instead of
    lexically before it. Full convention-aware numeric-vs-lexical robustness is
    Phase 2 (GRID-06) — keep this one line.
    """
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


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


def build_grid(index: SampleIndex, config: GridConfig) -> GridModel:
    """Build a dense Steps x Prompts lattice from a SampleIndex (Pattern 2)."""
    by_coord = {(s.dims[config.rows], s.dims[config.cols]): s for s in index}

    row_values = sorted({s.dims[config.rows] for s in index}, key=_as_number)
    col_values = sorted({s.dims[config.cols] for s in index})
    cell_ar = detect_universal_ar(index)

    cells: list[list[Cell]] = []
    for row in row_values:
        row_cells: list[Cell] = []
        for col in col_values:
            sample = by_coord.get((row, col))
            if sample is None:
                row_cells.append(Cell(CellState.MISSING))
            else:
                # BROKEN + ar_mismatch classification arrives in Plan 02.
                row_cells.append(Cell(CellState.POPULATED, sample=sample))
        cells.append(row_cells)

    return GridModel(
        row_values=row_values,
        col_values=col_values,
        cells=cells,
        cell_ar=cell_ar,
    )
