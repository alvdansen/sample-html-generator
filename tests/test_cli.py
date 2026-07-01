"""End-to-end CLI tests for the `grid build` walking skeleton."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

runner = CliRunner()


# Strings `detect` prints for eval-integrity signals; `build` must print NONE
# of them (D-02). Shared so the two tests can't drift.
_WARNING_STRINGS = (
    "source disagreement",
    "could not be classified",
    "cells hold multiple seeds",
    "seeds vary across grid",
)


def _seed_variance_folder(tmp_path: Path) -> Path:
    """A folder exercising per-coordinate alternates AND cross-cell seed variance
    plus one unclassifiable file.

    - ``p/step_200_seed7.png`` + ``p/step_200_seed42.png`` collide at (200, p) →
      the (200, p) cell holds alternates (lowest seed 7 wins).
    - ``p/step_600_seed99.png`` → a second populated cell whose chosen seed (99)
      differs from the first (7) → cross-cell seed variance.
    - ``p/notes.png`` → no integer step → skipped + counted (D-05).
    """
    from PIL import Image

    outputs = tmp_path / "variance"
    p = outputs / "p"
    p.mkdir(parents=True, exist_ok=True)
    for name in (
        "step_200_seed7.png",
        "step_200_seed42.png",
        "step_600_seed99.png",
        "notes.png",
    ):
        Image.new("RGB", (32, 18), (12, 34, 56)).save(p / name, format="PNG")
    return outputs


def test_detect_reports(tmp_path: Path) -> None:
    """META-05 / D-01: `grid detect` prints axes/counts/conflict/skip lines, BOTH
    the per-coordinate alternates line and the cross-cell seed-variance line when
    applicable, exits 0, and writes no index.html."""
    from sample_grid.cli.main import app

    folder = _seed_variance_folder(tmp_path)
    result = runner.invoke(app, ["detect", str(folder)])

    assert result.exit_code == 0, result.output
    out = result.output

    # Axes + their actual values, and populated/missing counts.
    assert "Rows (step): [200, 600]" in out
    assert "Cols (prompt): ['p']" in out
    assert "2 populated" in out

    # D-04 conflict line + D-05 skip line are always present (counts may be 0).
    assert "source disagreement" in out
    assert "could not be classified" in out
    assert "1 files could not be classified" in out  # notes.png skipped

    # Two DISTINCT seed signals both appear for this folder.
    assert "1 cells hold multiple seeds" in out          # per-coordinate (200, p)
    assert "seeds vary across grid: [7, 99]" in out       # cross-cell confound

    # detect renders nothing.
    assert not (folder / "grid-output").exists()
    assert not (tmp_path / "grid-output").exists()


def test_build_silent(tmp_path: Path) -> None:
    """D-02: `build` renders via auto-detect but prints none of the
    conflict/skip/multi-seed/cross-cell warning strings to stdout/stderr."""
    from sample_grid.cli.main import app

    folder = _seed_variance_folder(tmp_path)
    out_dir = tmp_path / "out"
    result = runner.invoke(
        app, ["build", str(folder), "-o", str(out_dir), "--no-open"]
    )

    assert result.exit_code == 0, result.output
    assert (out_dir / "grid-output" / "index.html").exists()

    for warning in _WARNING_STRINGS:
        assert warning not in result.output, f"build leaked warning: {warning!r}"


def test_build_writes_html(dense_sample_folder: Path, tmp_path: Path) -> None:
    """`grid build` on a dense folder writes a browser-openable index.html.

    The dense fixture is 2 prompts x 3 steps = 6 populated coordinates, so the
    rendered page must carry exactly 6 `<img` tags — one per populated cell.
    """
    # Imported inside the test so the module still *collects* before the
    # implementation exists (Task 1 RED): collection is green, the test is red.
    from sample_grid.cli.main import app

    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["build", str(dense_sample_folder), "-o", str(out), "--no-open"],
    )

    assert result.exit_code == 0, result.output

    index_html = out / "grid-output" / "index.html"
    assert index_html.exists(), f"expected {index_html} to be written"

    html = index_html.read_text(encoding="utf-8")
    assert html.count("<img") == 6, f"expected 6 <img tags, found {html.count('<img')}"


def test_build_empty_folder(tmp_path: Path) -> None:
    """Building an empty folder writes a valid empty-state page and exits 0."""
    from sample_grid.cli.main import app

    empty = tmp_path / "empty"
    empty.mkdir()
    out = tmp_path / "out"

    result = runner.invoke(
        app, ["build", str(empty), "-o", str(out), "--no-open"]
    )

    assert result.exit_code == 0, result.output

    index_html = out / "grid-output" / "index.html"
    assert index_html.exists(), f"expected {index_html} to be written"
    assert "No samples found" in index_html.read_text(encoding="utf-8")
