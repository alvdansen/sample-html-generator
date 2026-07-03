"""Grid-cell diff contract (RUN-04).

``diff(old_grid, new_grid)`` turns two built ``GridModel``s into a minimal,
structural-first-ordered list of ``Patch`` objects:

* a backfilled MISSING coordinate → exactly one ``replace_cell``,
* a new step → an ``insert_row`` at its numeric-sort index,
* a new prompt → an ``insert_col`` carrying the new column count,
* an identical re-scan → ``[]`` (idempotent ingest),
* mixed changes ordered ``insert_col`` → ``insert_row`` → ``replace_cell``.

These tests are Wave-0 RED: ``sample_grid.live.diff`` does not exist yet, so the
module import fails until Task 2 implements it.
"""
from __future__ import annotations

from pathlib import Path

from sample_grid.cli.main import _auto_parse
from sample_grid.core.grid import build_grid, natural_key
from sample_grid.core.model import (
    Cell,
    CellState,
    GridConfig,
    GridModel,
    Sample,
)

# RED until Task 2 creates sample_grid/live/diff.py.
from sample_grid.live.diff import Patch, diff


def _make_grid(steps, prompts, populated) -> GridModel:
    """Hand-build a dense Steps x Prompts GridModel.

    ``populated`` is the set of ``(step, prompt)`` coordinates that hold a
    POPULATED cell (id ``"<prompt>/step_<step>.png"``); every other coordinate is
    MISSING. Axes are natural-key sorted exactly like ``build_grid``.
    """
    row_values = sorted(steps, key=natural_key)
    col_values = sorted(prompts, key=natural_key)
    cells: list[list[Cell]] = []
    for step in row_values:
        row: list[Cell] = []
        for prompt in col_values:
            if (step, prompt) in populated:
                sid = f"{prompt}/step_{step}.png"
                sample = Sample(
                    id=sid,
                    path=Path(sid),
                    media_type="image",
                    dims={"step": step, "prompt": prompt},
                )
                row.append(Cell(CellState.POPULATED, sample=sample))
            else:
                row.append(Cell(CellState.MISSING))
        cells.append(row)
    return GridModel(
        row_values=row_values,
        col_values=col_values,
        cells=cells,
        cell_ar=(16, 9),
    )


def _coord_index(grid: GridModel, step, prompt) -> tuple[int, int]:
    return grid.row_values.index(step), grid.col_values.index(prompt)


def test_backfill_single_replace(sparse_sample_folder, dense_sample_folder, hole_coord):
    """A MISSING coordinate filled in produces exactly one replace_cell patch."""
    old_idx, _ = _auto_parse(sparse_sample_folder)
    new_idx, _ = _auto_parse(dense_sample_folder)
    old_grid = build_grid(old_idx, GridConfig())
    new_grid = build_grid(new_idx, GridConfig())

    patches = diff(old_grid, new_grid)

    assert len(patches) == 1
    (patch,) = patches
    assert patch.op == "replace_cell"

    r, c = _coord_index(new_grid, hole_coord["step"], hole_coord["prompt"])
    assert (patch.r, patch.c) == (r, c)
    # The backfilled coordinate went MISSING -> POPULATED.
    assert old_grid.cells[r][c].state is CellState.MISSING
    assert new_grid.cells[r][c].state is CellState.POPULATED


def test_axis_growth_patches():
    """A new step yields insert_row at its numeric index; a new prompt insert_col."""
    # New step (1000) appended to an all-populated grid.
    old = _make_grid([200, 600], ["p1"], {(200, "p1"), (600, "p1")})
    new = _make_grid([200, 600, 1000], ["p1"], {(200, "p1"), (600, "p1"), (1000, "p1")})
    row_patches = diff(old, new)
    assert len(row_patches) == 1
    (row_patch,) = row_patches
    assert row_patch.op == "insert_row"
    assert row_patch.step == 1000
    assert row_patch.index == 2  # numeric-sort position of 1000 in [200, 600, 1000]

    # New prompt column added to an all-populated grid.
    old_c = _make_grid([200, 600], ["p1"], {(200, "p1"), (600, "p1")})
    new_c = _make_grid(
        [200, 600], ["p1", "p2"],
        {(200, "p1"), (600, "p1"), (200, "p2"), (600, "p2")},
    )
    col_patches = diff(old_c, new_c)
    assert len(col_patches) == 1
    (col_patch,) = col_patches
    assert col_patch.op == "insert_col"
    assert col_patch.prompt == "p2"
    assert col_patch.index == 1
    assert col_patch.n_cols == 2  # the new total column count


def test_identical_rescan_no_patches(dense_sample_folder):
    """Two builds of the same folder diff to nothing (idempotent ingest)."""
    idx1, _ = _auto_parse(dense_sample_folder)
    idx2, _ = _auto_parse(dense_sample_folder)
    g1 = build_grid(idx1, GridConfig())
    g2 = build_grid(idx2, GridConfig())

    assert diff(g1, g2) == []


def test_structural_first_order():
    """A batch adding a col, a row, and a backfill orders col -> row -> cell."""
    old = _make_grid([200, 600], ["p1"], {(600, "p1")})  # (200, p1) is MISSING
    new = _make_grid(
        [200, 600, 1000], ["p1", "p2"],
        {
            (200, "p1"), (600, "p1"), (1000, "p1"),
            (200, "p2"), (600, "p2"), (1000, "p2"),
        },
    )

    patches = diff(old, new)
    ops = [p.op for p in patches]

    assert ops == ["insert_col", "insert_row", "replace_cell"]
    # The lone replace_cell is the backfilled (200, p1) coordinate.
    replace = patches[-1]
    r, c = _coord_index(new, 200, "p1")
    assert (replace.r, replace.c) == (r, c)


def test_sample_id_stable_across_rescan(dense_sample_folder):
    """Two scans of the same folder yield identical Sample.id sets (diff prereq)."""
    idx1, _ = _auto_parse(dense_sample_folder)
    idx2, _ = _auto_parse(dense_sample_folder)

    assert {s.id for s in idx1} == {s.id for s in idx2}
    # Sanity: the ids are non-empty (the folder is populated).
    assert idx1 and all(s.id for s in idx1)
