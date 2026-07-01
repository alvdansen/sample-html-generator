"""Core contracts for the grid pipeline.

These are the load-bearing shapes that every later phase depends on. Phase 1
ships one concrete parser and one concrete resolver, but the *types* here are the
stable seam: Phase 2 swaps the parser behind ``SampleIndex``; Phase 4/5 swap the
resolver behind ``render(GridModel, AssetResolver)``. Keep these shapes exact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class CellState(Enum):
    """Explicit per-coordinate state, classified in Python (never inferred in JS)."""

    POPULATED = "populated"  # a sample exists and renders at this coordinate
    MISSING = "missing"      # no file at this (step, prompt) coordinate    (D-09)
    BROKEN = "broken"        # file present but won't decode                (D-10)


@dataclass(frozen=True)
class Sample:
    """One discovered media sample, located on a (step, prompt) coordinate.

    ``dims`` carries the grouping dimensions, e.g. ``{"step": 600, "prompt": "a lake"}``.
    Frozen so a ``Sample`` is hashable and safe to share across the index.
    """

    id: str           # stable id — P1: posix-relative path ("<prompt>/<file>")
    path: Path        # absolute (or cwd-relative) path to the media file on disk
    media_type: str   # "image" or "video" (video live from P3 via media_type_for)
    dims: dict        # {"step": int, "prompt": str}


# The parser seam: a flat list of samples. Phase 2 swaps how this is produced.
SampleIndex = list[Sample]


@dataclass
class Cell:
    """A single lattice coordinate. ``sample`` is None for MISSING coordinates."""

    state: CellState
    sample: "Sample | None" = None
    ar_mismatch: bool = False  # True -> letterbox fallback (D-11); set in Plan 02
    has_alternates: bool = False  # D-10 — >1 sample resolved to this coordinate
    # D-09 feed — the seeds of all samples that collided at this coordinate.
    alternate_seeds: list = field(default_factory=list)


@dataclass
class GridConfig:
    """Which dimension maps to rows vs columns. D-04: steps down, prompts across."""

    rows: str = "step"   # D-04 — steps descend the rows (left axis)
    cols: str = "prompt"  # D-04 — prompts span the columns (top axis)


@dataclass
class GridModel:
    """A dense Steps x Prompts lattice — one Cell per coordinate, never collapsed."""

    row_values: list           # ordered row-axis values (steps, numeric-sorted)
    col_values: list           # ordered col-axis values (prompts)
    cells: list                # cells[row][col]; dense — len == rows x cols
    cell_ar: "tuple[int, int] | float"  # detected universal aspect ratio (D-11)
    # D-09 — seeds vary across the grid: per-coordinate multi-seed OR a cross-cell
    # seed confound (distinct seeds across single-sample cells). Classified in
    # Python at build time, never inferred in JS.
    seed_varies: bool = False
