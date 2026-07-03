"""Grid-cell diff → minimal ``Patch`` list (RUN-04).

``diff(old_grid, new_grid)`` compares two built :class:`GridModel`s and emits the
smallest set of structural mutations that turns the old grid into the new one:

* a coordinate present in **both** grids whose rendered ``Cell`` changed →
  a ``replace_cell`` patch at the new ``(r, c)`` index (the dominant live op —
  the fixed lattice already has a MISSING node at every coordinate, so a backfill
  is an in-place swap, not an insertion);
* a step in ``new.row_values`` absent from the old grid → an ``insert_row`` at its
  numeric-sort position;
* a prompt in ``new.col_values`` absent from the old grid → an ``insert_col`` at
  its numeric-sort position, carrying the new total column count.

The diff runs over the **built** ``GridModel`` — never the raw ``SampleIndex`` —
because the rendered winner at a coordinate is chosen by ``build_grid`` (lowest
seed) and carries ``ar_mismatch`` / ``has_alternates``. Comparing the built cell
is what makes a patch mirror exactly what a full re-render would produce.

Patches are ordered **structural-first**: every ``insert_col`` before every
``insert_row`` before every ``replace_cell`` — so the client bumps ``--n-cols``
(gaining a grid track) before per-row cells arrive. The ``Patch`` envelope carries
only ``op`` + coordinates + ``n_cols``; every scrap of markup is server-rendered
downstream (04-03), keeping "state in Python, JS only mutates DOM."
"""
from __future__ import annotations

from dataclasses import dataclass

from sample_grid.core.grid import natural_key
from sample_grid.core.model import Cell, GridModel


@dataclass
class Patch:
    """One structural mutation of the live grid — the SSE/DOM patch envelope.

    ``op`` selects the fields that carry meaning; unused fields stay ``None``:

    * ``replace_cell`` → ``r``, ``c`` (the new-grid cell indices to swap in place);
    * ``insert_row``   → ``index`` (numeric-sort row position), ``step`` (its value);
    * ``insert_col``   → ``index`` (numeric-sort col position), ``prompt`` (its
      value), ``n_cols`` (the new total column count).

    No rendered HTML lives here — the server fills markup from the new grid.
    """

    op: str
    r: "int | None" = None
    c: "int | None" = None
    index: "int | None" = None
    step: object = None
    prompt: object = None
    n_cols: "int | None" = None


def _sample_id(cell: Cell) -> "str | None":
    """Stable id of a cell's winning sample, or ``None`` for a MISSING cell."""
    return cell.sample.id if cell.sample is not None else None


def _cell_differs(old: Cell, new: Cell) -> bool:
    """True when a coordinate's rendered cell changed between builds.

    Compares the fields that drive the rendered markup: ``state``, the winning
    ``sample.id`` (None-safe — a lowest-seed winner change swaps the id), and the
    ``has_alternates`` / ``ar_mismatch`` flags.
    """
    return (
        old.state != new.state
        or _sample_id(old) != _sample_id(new)
        or old.has_alternates != new.has_alternates
        or old.ar_mismatch != new.ar_mismatch
    )


def diff(old_grid: GridModel, new_grid: GridModel) -> "list[Patch]":
    """Return the minimal, structural-first-ordered patches from old → new grid."""
    old_rows = list(old_grid.row_values)
    old_cols = list(old_grid.col_values)
    new_rows = list(new_grid.row_values)
    new_cols = list(new_grid.col_values)
    old_rowset = set(old_rows)
    old_colset = set(old_cols)

    # Numeric-aware axis order so a mid-run step/prompt slots into its true
    # position (ROADMAP criterion 4), never appended blindly. row_values/col_values
    # are already natural-key sorted by build_grid; re-sort defensively for index.
    sorted_new_rows = sorted(new_rows, key=natural_key)
    sorted_new_cols = sorted(new_cols, key=natural_key)

    # (1) New columns → insert_col (structural, emitted first). Iterating the
    # already-sorted new_cols yields ascending index order.
    col_patches: list[Patch] = [
        Patch(
            op="insert_col",
            index=sorted_new_cols.index(prompt),
            prompt=prompt,
            n_cols=len(new_cols),
        )
        for prompt in new_cols
        if prompt not in old_colset
    ]

    # (2) New rows → insert_row.
    row_patches: list[Patch] = [
        Patch(op="insert_row", index=sorted_new_rows.index(step), step=step)
        for step in new_rows
        if step not in old_rowset
    ]

    # (3) Coordinates present in BOTH grids whose built cell changed → replace_cell.
    old_cell: dict = {
        (step, prompt): old_grid.cells[i][j]
        for i, step in enumerate(old_rows)
        for j, prompt in enumerate(old_cols)
    }
    replace_patches: list[Patch] = []
    for i, step in enumerate(new_rows):
        if step not in old_rowset:
            continue
        for j, prompt in enumerate(new_cols):
            if prompt not in old_colset:
                continue
            old = old_cell.get((step, prompt))
            new = new_grid.cells[i][j]
            if old is not None and _cell_differs(old, new):
                replace_patches.append(Patch(op="replace_cell", r=i, c=j))

    # Structural-first: all insert_col, then insert_row, then replace_cell.
    return col_patches + row_patches + replace_patches
