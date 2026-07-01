"""Build-time correctness for the fixed lattice + per-cell classification.

These tests pin the single most important property of the phase (RESEARCH
Pitfall 1/2): every coordinate is classified in Python at build time —
POPULATED / MISSING / BROKEN and the ``ar_mismatch`` flag — with the lattice
never collapsing. They also prove the parser-free ``build_grid`` seam (P2).
"""
from __future__ import annotations

from pathlib import Path

from sample_grid.core.grid import (
    build_grid,
    detect_universal_ar,
    is_decodable,
    natural_key,
)
from sample_grid.core.model import (
    Cell,
    CellState,
    GridConfig,
    GridModel,
    Sample,
)
from sample_grid.core.parse.filename import FilenameStubParser
from sample_grid.core.scan import Scanner


def _index(folder: Path):
    """Scan + parse a folder into a SampleIndex (mirrors the CLI pipeline)."""
    return FilenameStubParser().parse(Scanner().scan(folder))


def _flat(grid: GridModel) -> list[Cell]:
    return [cell for row in grid.cells for cell in row]


def _states(grid: GridModel) -> list[CellState]:
    return [cell.state for cell in _flat(grid)]


def test_is_decodable_true_for_valid_false_for_corrupt(tmp_path: Path) -> None:
    from PIL import Image

    good = tmp_path / "good.png"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(good, format="PNG")
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"this is not a png")

    assert is_decodable(good) is True
    assert is_decodable(bad) is False  # no raise — just False


def test_natural_sort(unpadded_step_folder: Path) -> None:
    """GRID-06 / D-11: unpadded numeric axes sort by magnitude, not lexically."""
    # Direct unit assert on the key: lexical would give [1000, 200, 30000].
    assert sorted(
        ["step_1000", "step_200", "step_30000"], key=natural_key
    ) == ["step_200", "step_1000", "step_30000"]

    # Pure-int values sort ahead of any non-numeric label (numeric-first tier).
    assert sorted([30000, "step_5", 200], key=natural_key) == [200, 30000, "step_5"]

    # End-to-end through build_grid: the derived row axis is numerically ordered.
    grid = build_grid(_index(unpadded_step_folder), GridConfig())
    assert grid.row_values == [200, 1000, 30000]


def test_orientation_steps_rows(dense_sample_folder: Path, grid_axes: dict) -> None:
    """D-04: rows are the numerically-sorted steps; columns are the prompts."""
    grid = build_grid(_index(dense_sample_folder), GridConfig())
    assert grid.row_values == sorted(grid_axes["steps"])
    assert sorted(grid.col_values) == sorted(grid_axes["prompts"])


def test_fixed_lattice_sparse(sparse_sample_folder: Path) -> None:
    """GRID-05: a missing coordinate never collapses the rows x cols lattice."""
    grid = build_grid(_index(sparse_sample_folder), GridConfig())

    rows, cols = len(grid.row_values), len(grid.col_values)
    assert len(grid.cells) == rows
    assert all(len(row) == cols for row in grid.cells)
    assert len(_flat(grid)) == rows * cols

    missing = [c for c in _flat(grid) if c.state == CellState.MISSING]
    assert len(missing) == 1
    assert missing[0].sample is None


def test_missing_vs_broken_distinct(
    sparse_sample_folder: Path, corrupt_sample_folder: Path
) -> None:
    """D-09/D-10: absent file -> MISSING (no sample); corrupt file -> BROKEN (sample set)."""
    sparse = build_grid(_index(sparse_sample_folder), GridConfig())
    corrupt = build_grid(_index(corrupt_sample_folder), GridConfig())

    missing = [c for c in _flat(sparse) if c.state == CellState.MISSING]
    broken = [c for c in _flat(corrupt) if c.state == CellState.BROKEN]

    assert len(missing) == 1 and missing[0].sample is None
    assert len(broken) == 1 and broken[0].sample is not None
    # The two states are genuinely different classifications.
    assert CellState.MISSING != CellState.BROKEN
    # The corrupt grid has NO missing cell (the file is present) and the sparse
    # grid has NO broken cell — the classifications do not bleed into each other.
    assert not any(c.state == CellState.MISSING for c in _flat(corrupt))
    assert not any(c.state == CellState.BROKEN for c in _flat(sparse))


def test_universal_ar_and_mismatch(
    stray_ar_sample_folder: Path, hole_coord: dict
) -> None:
    """D-11: dominant AR detected; the stray-AR cell flags ar_mismatch, others don't."""
    index = _index(stray_ar_sample_folder)
    grid = build_grid(index, GridConfig())

    # The uniform 32x18 (~16:9) images dominate; (16, 9) is the universal AR.
    assert detect_universal_ar(index) == (16, 9)
    assert grid.cell_ar == (16, 9)

    mismatched = [
        (ri, ci)
        for ri, row in enumerate(grid.cells)
        for ci, cell in enumerate(row)
        if cell.state == CellState.POPULATED and cell.ar_mismatch
    ]
    assert len(mismatched) == 1

    ri, ci = mismatched[0]
    assert grid.row_values[ri] == hole_coord["step"]
    assert grid.col_values[ci] == hole_coord["prompt"]


def test_build_grid_from_handbuilt_index(tmp_path: Path) -> None:
    """P2 seam: a hand-built SampleIndex (no parser) yields a correct GridModel."""
    from PIL import Image

    def _img(name: str) -> Path:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 18), (12, 34, 56)).save(p, format="PNG")
        return p

    index = [
        Sample(id="p/a.png", path=_img("p/a.png"), media_type="image",
               dims={"step": 200, "prompt": "p"}),
        Sample(id="q/b.png", path=_img("q/b.png"), media_type="image",
               dims={"step": 200, "prompt": "q"}),
        Sample(id="p/c.png", path=_img("p/c.png"), media_type="image",
               dims={"step": 600, "prompt": "p"}),
        # (step=600, prompt="q") deliberately absent -> one MISSING cell.
    ]
    grid = build_grid(index, GridConfig())

    assert grid.row_values == [200, 600]
    assert sorted(grid.col_values) == ["p", "q"]
    assert len(_flat(grid)) == 2 * 2  # fixed lattice from the contract alone
    assert sum(1 for c in _flat(grid) if c.state == CellState.POPULATED) == 3
    assert sum(1 for c in _flat(grid) if c.state == CellState.MISSING) == 1
